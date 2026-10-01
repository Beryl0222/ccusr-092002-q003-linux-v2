"""现场处置：把当天 operations 日志按时间回放到排班结果上。

事件必须带稳定 id；同一 id（包括离线终端网络恢复后的重试）只生效一次，
因此重复核销不会再次计费。每一次处置都产生一条审计决策和必要的游客通知。
"""

from dataclasses import dataclass

from .model import to_hhmm, to_minutes
from .planner import Booking, Decision, Notice


@dataclass
class CheckinRecord:
    event_id: str
    at: str
    occurred_at: str
    group: str
    resource: str
    content: str
    scheduled_start: str
    participants: int
    offline: dict | None
    note: str
    duplicate_of: str | None = None

    def booking_key(self):
        return (self.group, self.resource, self.content, to_minutes(self.scheduled_start))


@dataclass
class EngineResult:
    bookings: list
    decisions: list
    notices: list
    checkins: list
    ignored_events: list

    def valid_checkins(self):
        return [c for c in self.checkins if c.duplicate_of is None]

    def booking(self, booking_id):
        return next((b for b in self.bookings if b.booking_id == booking_id), None)


class DayEngine:
    def __init__(self, bundle, plan_result):
        self.bundle = bundle
        self.bookings = plan_result.bookings
        self.occupancy = plan_result.occupancy
        self.decisions = list(plan_result.decisions)
        self.notices: list[Notice] = list(getattr(plan_result, "notices", []))
        self.checkins: list[CheckinRecord] = []
        self.ignored: list = []
        self._seen_ids: dict = {}
        self._seq = len(self.decisions)

    def _decision(self, event, group, code, title, detail, result, booking=None):
        self._seq += 1
        d = Decision(self._seq, "operations", group, code, title, detail, result,
                     request_ref={"event_id": event.get("id"), "type": event.get("type")},
                     booking_id=booking.booking_id if booking else None)
        self.decisions.append(d)
        return d

    def _find_booking(self, group, resource, content, scheduled_start):
        target = to_minutes(scheduled_start)
        for b in self.bookings:
            if (b.group == group and b.resource == resource and b.content == content
                    and b.start == target):
                return b
        # 改道后的排班以新内容登记；退化为只按团队+原资源匹配
        for b in self.bookings:
            if b.group == group and b.resource == resource and b.start == target:
                return b
        return None

    def apply(self):
        events = sorted(self.bundle.operations, key=lambda e: to_minutes(e["at"]))
        for event in events:
            self._apply_one(event)
        return EngineResult(
            bookings=self.bookings,
            decisions=self.decisions,
            notices=self.notices,
            checkins=self.checkins,
            ignored_events=self.ignored,
        )

    def _apply_one(self, event):
        eid = event.get("id")
        if eid is not None and eid in self._seen_ids:
            self.ignored.append({"event_id": eid, "reason": "duplicate_event_id"})
            self._decision(
                event, event.get("group"), "duplicate_suppressed",
                f"重复事件 {eid} 已忽略",
                "事件 id 已处理过（离线终端网络恢复后的重试），不重复占用容量、不重复计费。",
                "suppressed",
            )
            return
        if eid is not None:
            self._seen_ids[eid] = event

        handler = {
            "headcount": self._ev_headcount,
            "late_arrival": self._ev_late,
            "checkin": self._ev_checkin,
            "stop": self._ev_stop,
            "reroute": self._ev_reroute,
            "cancel": self._ev_cancel,
        }.get(event["type"])
        if handler is None:
            self._decision(event, event.get("group"), "unknown_event",
                           f"未知事件类型 {event['type']}", "未做任何处理。", "ignored")
            return
        handler(event)

    # ---- 事件处理 ------------------------------------------------------

    def _ev_headcount(self, event):
        group = self.bundle.groups[event["group"]]
        old = group.get("size")
        group["size"] = int(event["actual"])
        self._decision(event, event["group"], "headcount_updated",
                       f"{event['group']} 现场实到 {event['actual']} 人",
                       f"申报人数 {old}，现场核定 {event['actual']} 人，后续容量与分账以实到人数为准。",
                       "applied")

    def _ev_late(self, event):
        b = self._find_booking(event["group"], event["resource"],
                               event["content"], event["scheduled_start"])
        if b is None:
            self._decision(event, event["group"], "late_without_booking",
                           "迟到事件找不到对应排班", "未做任何处理。", "ignored")
            return
        minutes = int(event.get("minutes", 0))
        b.status = "delayed"
        b.actual_start = b.start + minutes
        b.note = (b.note + f"；迟到 {minutes} 分钟").strip("；")
        self._decision(
            event, event["group"], "late_arrival",
            f"{event['group']} 迟到 {minutes} 分钟",
            f"《{self.bundle.contents[b.content]['name']}》开始时间顺延至 "
            f"{to_hhmm(b.actual_start)}，结束时间相应顺延，仍在资源开放时段内。",
            "applied", b,
        )
        self.notices.append(Notice(
            at=event["at"], group=event["group"], level="info", kind="late",
            title="开演时间顺延通知",
            detail=f"您预约的《{self.bundle.contents[b.content]['name']}》因团队迟到，"
                   f"顺延至 {to_hhmm(b.actual_start)} 开始，请按新时间前往。",
            booking_id=b.booking_id,
        ))

    def _ev_checkin(self, event):
        offline = event.get("offline")
        b = self._find_booking(event["group"], event["resource"],
                               event["content"], event["scheduled_start"])
        if b is None:
            self._decision(event, event["group"], "checkin_without_booking",
                           "核销找不到对应排班", "该核销被拒绝，不计费。", "rejected")
            return
        if b.status in ("cancelled", "stopped", "rerouted"):
            self._decision(
                event, event["group"], "checkin_after_cancel",
                "核销时该场次已取消/停演/改道",
                f"《{self.bundle.contents[b.content]['name']}》状态为 {b.status}，核销拒绝、不计费。",
                "rejected", b,
            )
            return

        record = CheckinRecord(
            event_id=event["id"],
            at=event["at"],
            occurred_at=event.get("occurred_at", event["at"]),
            group=event["group"],
            resource=b.resource,
            content=b.content,
            scheduled_start=to_hhmm(b.start),
            participants=int(event["actual"]),
            offline=offline,
            note=event.get("note", ""),
        )
        self.checkins.append(record)
        b.actual_start = b.actual_start or to_minutes(record.occurred_at)
        sync_note = ""
        if offline:
            sync_note = (f"该核销于 {offline.get('recorded_at')} 离线记录，"
                         f"{offline.get('synced_at')} 网络恢复后同步，以实际发生时间入账。")
        self._decision(
            event, event["group"], "checked_in",
            f"核销成功：{event['group']}《{self.bundle.contents[b.content]['name']}》",
            f"实际参与 {record.participants} 人，按此人数结算。{sync_note}".strip(),
            "applied", b,
        )
        if offline:
            self.notices.append(Notice(
                at=event["at"], group=event["group"], level="info", kind="offline_synced",
                title="离线核销已同步",
                detail=f"您在 {offline.get('recorded_at')} 的《"
                       f"{self.bundle.contents[b.content]['name']}》核销已在网络恢复后同步，"
                       f"实际参与 {record.participants} 人，仅计费一次。",
                booking_id=b.booking_id,
            ))

    def _ev_stop(self, event):
        resource_id = event["resource"]
        at_min = to_minutes(event["from"])
        affected = [b for b in self.bookings
                    if b.resource == resource_id and b.end > at_min
                    and b.status == "scheduled"]
        res = self.bundle.resources[resource_id]
        res["stopped_from"] = event["from"]
        for b in affected:
            b.status = "stopped"
            self.occupancy.remove(b)
            self.notices.append(Notice(
                at=event["at"], group=b.group, level="cancel", kind="stop",
                title="停演通知",
                detail=f"{res['name']}自 {event['from']} 起停演：{event.get('reason', '现场原因')}。"
                       f"您预约的《{self.bundle.contents[b.content]['name']}》"
                       f"{to_hhmm(b.start)} 场次无法进行，正在安排替代方案。",
                booking_id=b.booking_id,
            ))
        names = "、".join(f"{b.group} {to_hhmm(b.start)}" for b in affected) or "无在途团队"
        self._decision(
            event, None, "resource_stopped",
            f"{res['name']} 自 {event['from']} 停演",
            f"原因：{event.get('reason', '现场原因')}。受影响排班：{names}。"
            f"相关场次容量即时释放，不计收入，等待改道或取消。",
            "applied",
        )

    def _ev_reroute(self, event):
        old = self._find_booking(event["group"], event["from_resource"],
                                 event["content"], event["scheduled_start"])
        if old is None:
            self._decision(event, event["group"], "reroute_without_booking",
                           "改道找不到原排班", "改道未执行。", "rejected")
            return
        new_res = self.bundle.resources[event["to_resource"]]
        new_content = self.bundle.contents[event["to_content"]]
        start = to_minutes(event["new_start"])
        end = start + new_content["duration_minutes"]
        size = self.bundle.groups[event["group"]]["size"]
        units = 1 if new_res.get("capacity_unit") == "team" else size
        clash = self.occupancy.conflict(
            new_res["id"], start, end, units,
            new_res.get("capacity", 1), new_res.get("capacity_unit", "team"),
        )
        if clash:
            self._decision(
                event, event["group"], "reroute_target_full",
                "改道目标场次承载不足",
                f"{new_res['name']} {event['new_start']} 与现有排班冲突，改道未执行，"
                f"原场次保持取消状态。",
                "rejected", old,
            )
            return
        window_ok = any(to_minutes(s) <= start and end <= to_minutes(e)
                        for s, e in new_res.get("sessions", []))
        if not window_ok:
            self._decision(event, event["group"], "reroute_outside_hours",
                           "改道目标不在开放时段", "改道未执行。", "rejected", old)
            return

        old.status = "rerouted"
        self.occupancy.remove(old)
        new_b = Booking(
            booking_id=f"B-R{len(self.bookings) + 1:03d}",
            group=event["group"], resource=new_res["id"],
            content=new_content["id"], start=start, end=end, team_size=size,
            status="scheduled", note=f"由 {old.booking_id} 改道：{event.get('reason', '')}",
        )
        self.bookings.append(new_b)
        self.occupancy.add(new_b, units)
        self._decision(
            event, event["group"], "rerouted",
            f"{event['group']} 改道至{new_res['name']}",
            f"原《{self.bundle.contents[event['content']]['name']}》"
            f"({self.bundle.resources[event['from_resource']]['name']} "
            f"{event['scheduled_start']}) 取消；原因：{event.get('reason', '现场原因')}。"
            f"新安排《{new_content['name']}》{event['new_start']} 开始，"
            f"按新项目价格与实际参与人数结算。",
            "applied", new_b,
        )
        self.notices.append(Notice(
            at=event["at"], group=event["group"], level="change", kind="reroute",
            title="行程变更通知",
            detail=f"因{event.get('reason', '现场原因')}，{event['scheduled_start']} 的"
                   f"《{self.bundle.contents[event['content']]['name']}》调整为 "
                   f"{event['new_start']} 在{new_res['name']}进行的"
                   f"《{new_content['name']}》，请按新行程前往。",
            booking_id=new_b.booking_id,
        ))

    def _ev_cancel(self, event):
        b = self._find_booking(event["group"], event["resource"],
                               event["content"], event["scheduled_start"])
        if b is None:
            self._decision(event, event["group"], "cancel_without_booking",
                           "取消找不到对应排班", "未做任何处理。", "ignored")
            return
        b.status = "cancelled"
        self.occupancy.remove(b)
        self._decision(
            event, event["group"], "cancelled",
            f"{event['group']} 取消《{self.bundle.contents[b.content]['name']}》",
            f"取消方：{event.get('by', '现场')}；原因：{event.get('reason', '现场原因')}。"
            f"容量即时释放给其他团队，该场次不计费、不参与分账。",
            "applied", b,
        )
        self.notices.append(Notice(
            at=event["at"], group=event["group"], level="cancel", kind="cancel",
            title="场次取消确认",
            detail=f"您 {event['scheduled_start']} 在"
                   f"{self.bundle.resources[b.resource]['name']}的"
                   f"《{self.bundle.contents[b.content]['name']}》已取消"
                   f"（{event.get('reason', '现场原因')}），该场次不收取费用。",
            booking_id=b.booking_id,
        ))
