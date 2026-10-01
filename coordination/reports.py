"""三类交付物的生成：游客通知、收益明细、调度决策（含机器可读 JSON）。"""

import json
from dataclasses import dataclass

from .model import to_hhmm, yuan
from .planner import plan_requests
from .engine import DayEngine
from .settlement import settle

RESULT_LABELS = {
    "accepted": "成立",
    "substituted": "替代安排",
    "reassigned": "改约",
    "rejected": "拒绝",
    "suppressed": "抑制",
    "applied": "已执行",
    "ignored": "忽略",
}

STATUS_LABELS = {
    "scheduled": "已排定",
    "delayed": "迟到顺延",
    "rerouted": "已改道（旧场次）",
    "cancelled": "已取消",
    "stopped": "停演",
}


@dataclass
class DayRun:
    bundle: object
    plan: object
    engine_result: object
    settlement: object

    def as_dict(self):
        b = self.bundle
        return {
            "date": b.date,
            "currency": b.currency,
            "final_schedule": [_booking_dict(b, x) for x in self.engine_result.bookings],
            "notices": [n.as_dict() for n in self.engine_result.notices],
            "settlement": self.settlement.as_dict(b.currency),
            "decisions": [d.as_dict() for d in self.engine_result.decisions],
            "ignored_events": self.engine_result.ignored_events,
        }


def _booking_dict(bundle, booking):
    content = bundle.contents.get(booking.content, {"name": booking.content})
    res = bundle.resources.get(booking.resource, {"name": booking.resource})
    return {
        "booking_id": booking.booking_id,
        "group": booking.group,
        "resource": booking.resource,
        "resource_name": res["name"],
        "content": booking.content,
        "content_name": content["name"],
        "start": to_hhmm(booking.start),
        "end": to_hhmm(booking.end),
        "status": booking.status,
        "actual_start": to_hhmm(booking.actual_start) if booking.actual_start else None,
        "team_size": booking.team_size,
        "walk_minutes": booking.walk_minutes,
        "walk_path": booking.walk_path,
        "walk_detour": booking.walk_detour,
        "note": booking.note,
    }


def run_day(bundle):
    plan = plan_requests(bundle)
    engine = DayEngine(bundle, plan)
    engine_result = engine.apply()
    settlement = settle(bundle, engine_result)
    return DayRun(bundle, plan, engine_result, settlement)


# ----------------------------------------------------------------------
# Markdown 交付物
# ----------------------------------------------------------------------

def notices_markdown(run: DayRun) -> str:
    bundle = run.bundle
    lines = [f"# 游客变更通知（{bundle.date}）", ""]
    notices = run.engine_result.notices
    if not notices:
        lines += ["当天无变更通知。", ""]
        return "\n".join(lines)
    for group_id in bundle.groups:
        group_notices = [n for n in notices if n.group == group_id]
        if not group_notices:
            continue
        g = bundle.groups[group_id]
        lines.append(f"## {group_id}（{g.get('contact', '')}，{g['size']} 人）")
        lines.append("")
        for n in group_notices:
            tag = {"change": "【行程变更】", "cancel": "【取消/停演】", "info": "【通知】"}.get(
                n.level, "【通知】")
            lines.append(f"- {n.at} {tag} **{n.title}**")
            lines.append(f"  - {n.detail}")
        lines.append("")
    lines.append("> 通知由现场核销、停演、改道与取消事件即时生成；离线核销在网络恢复同步后补发。")
    return "\n".join(lines) + "\n"


def settlement_markdown(run: DayRun) -> str:
    bundle = run.bundle
    st = run.settlement
    cur = bundle.currency
    lines = [f"# 收益明细（{bundle.date}，{cur}）", "",
             "仅统计**实际核销且参与者到场**的场次；取消、停演、未到场与重复核销均不计入。", ""]
    lines.append("## 逐笔收入")
    lines.append("")
    lines.append("| 核销事件 | 团队 | 场所/传承人 | 项目 | 实际人数 | 单价 | 毛收入 | 离线 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for line in st.lines:
        lines.append(
            f"| {line.event_id} | {line.group} | {line.resource_name} | "
            f"{line.content_name} | {line.participants} | {yuan(line.unit_price_cents)} | "
            f"{yuan(line.gross_cents)} | {'是' if line.offline else ''} |"
        )
    lines.append("")

    lines.append("## 分账明细（按申报份额）")
    lines.append("")
    lines.append("| 核销事件 | 毛收入 | " + " | ".join(bundle.payees[p]["name"] for p in bundle.payees) + " |")
    lines.append("| --- | --- | " + " | ".join(["---"] * len(bundle.payees)) + " |")
    for line in st.lines:
        cells = []
        for p in bundle.payees:
            amount = line.splits.get(p)
            cells.append(yuan(amount) if amount is not None else "—")
        lines.append(f"| {line.event_id} | {yuan(line.gross_cents)} | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## 收款方合计")
    lines.append("")
    lines.append("| 收款方 | 到账金额 |")
    lines.append("| --- | --- |")
    for pid, total in st.payee_totals.items():
        lines.append(f"| {bundle.payees[pid]['name']} | {yuan(total)} |")
    lines.append(f"| **合计** | **{yuan(sum(st.payee_totals.values()))}** |")
    lines.append("")

    lines.append("## 团队应付")
    lines.append("")
    for gid, total in st.group_totals.items():
        lines.append(f"- {gid}：{yuan(total)} {cur}")
    lines.append("")

    if st.skipped:
        lines.append("## 挂起/剔除记录")
        lines.append("")
        for s in st.skipped:
            lines.append(f"- {s['event_id']}：{s['reason']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def decisions_markdown(run: DayRun) -> str:
    bundle = run.bundle
    lines = [f"# 调度决策记录（{bundle.date}）", "",
             "记录每一次排班成立、替代、改约、拒绝与现场处置的具体原因，可供管理审计。", ""]
    stage_label = {"planning": "排班阶段", "operations": "现场阶段"}
    for d in run.engine_result.decisions:
        result = RESULT_LABELS.get(d.result, d.result)
        lines.append(f"## {d.seq}. [{stage_label.get(d.stage, d.stage)}] {d.title}（{result}）")
        lines.append("")
        lines.append(f"- 对象：{d.group or '全体/资源方'}")
        lines.append(f"- 判定码：`{d.code}`")
        if d.booking_id:
            lines.append(f"- 关联排班：{d.booking_id}")
        if d.request_ref:
            ref_items = [f"{k}={v}" for k, v in d.request_ref.items()]
            lines.append(f"- 来源：{', '.join(ref_items)}")
        lines.append(f"- 原因：{d.detail}")
        lines.append("")

    ignored = run.engine_result.ignored_events
    if ignored:
        lines.append("## 幂等抑制")
        lines.append("")
        for ig in ignored:
            lines.append(f"- {ig['event_id']}：{ig['reason']}（不改变容量、不重复计费）")
        lines.append("")
    return "\n".join(lines) + "\n"


def final_schedule_markdown(run: DayRun) -> str:
    bundle = run.bundle
    lines = [f"# 最终执行排班（{bundle.date}）", ""]
    lines.append("| 排班 | 团队 | 时间 | 场所/传承人 | 项目 | 状态 | 实到/人数 | 备注 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for b in sorted(run.engine_result.bookings, key=lambda x: (x.start, x.group)):
        d = run.bundle.contents.get(b.content, {"name": b.content})
        r = bundle.resources.get(b.resource, {"name": b.resource})
        time_txt = to_hhmm(b.start)
        if b.actual_start:
            time_txt += f"（实际 {to_hhmm(b.actual_start)}）"
        lines.append(
            f"| {b.booking_id} | {b.group} | {time_txt} | {r['name']} | {d['name']} | "
            f"{STATUS_LABELS.get(b.status, b.status)} | {b.team_size} | {b.note} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def build_deliverables(bundle, out_dir):
    """执行完整一天并把交付物写入 out_dir，返回 DayRun。"""
    from pathlib import Path
    run = run_day(bundle)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    artifacts = {
        "notices": notices_markdown(run),
        "settlement": settlement_markdown(run),
        "decisions": decisions_markdown(run),
        "schedule": final_schedule_markdown(run),
    }
    for name, text in artifacts.items():
        (out / f"{name}.md").write_text(text, encoding="utf-8")
    (out / "day_result.json").write_text(
        json.dumps(run.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return run
