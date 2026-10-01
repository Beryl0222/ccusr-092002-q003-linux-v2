"""民族文旅承载协作台的服务入口。

- `python3 service.py --check`：服务身份自检；
- `python3 service.py --run-day`：回放 contracts/visit_day.json 的一天，
  把游客通知、收益明细、调度决策写入 out/；
- `python3 service.py`：启动 HTTP 服务，提供只读查询与现场事件上报。
"""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from coordination.model import load_bundle
from coordination.reports import build_deliverables, run_day

SERVICE_ID = "heritage-visit-coordination"
SERVICE_NAME = "民族文旅承载协作台"
DEFAULT_DAY = "contracts/visit_day.json"
DEFAULT_OUT = "out"


def health_payload():
    """返回稳定的服务身份信息。"""
    return {"status": "ok", "service": SERVICE_ID, "name": SERVICE_NAME}


class DayState:
    """一日协作状态：事件日志是唯一事实源，每次重放得到确定结果。"""

    def __init__(self, day_path):
        self.day_path = day_path
        self._lock = threading.Lock()
        self.reload()

    def reload(self):
        with self._lock:
            self.bundle = load_bundle(self.day_path)
            return self._snapshot_locked()

    def snapshot(self):
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self):
        return run_day(self.bundle)

    def add_events(self, events):
        """把一条或多条现场事件追加进日志后整体重放。"""
        if isinstance(events, dict):
            events = [events]
        with self._lock:
            existing_ids = {e.get("id") for e in self.bundle.operations if e.get("id")}
            accepted = []
            duplicates = []
            for ev in events:
                if not isinstance(ev, dict) or "type" not in ev:
                    return 400, {"error": "每个事件必须是对象且包含 type"}
                eid = ev.get("id")
                if eid is not None and eid in existing_ids:
                    duplicates.append(eid)
                    continue
                if eid is not None:
                    existing_ids.add(eid)
                self.bundle.operations.append(ev)
                accepted.append(eid)
            run = self._snapshot_locked()
            return 200, {
                "accepted_event_ids": accepted,
                "duplicate_event_ids": duplicates,
                "notices": [n.as_dict() for n in run.engine_result.notices],
                "ignored_events": run.engine_result.ignored_events,
            }


class Handler(BaseHTTPRequestHandler):
    """健康检查与一日协作查询/事件上报。"""

    state = None  # 由 main 注入

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(200, health_payload())
            return
        if self.state is None:
            self.send_error(503, "未加载当日数据")
            return
        run = self.state.snapshot()
        if path == "/plan":
            self._send_json(200, {
                "date": run.bundle.date,
                "bookings": [_schedule_item(run, b) for b in run.engine_result.bookings],
                "decisions": [d.as_dict() for d in run.plan.decisions],
                "notices": [n.as_dict() for n in run.plan.notices],
            })
        elif path == "/schedule":
            self._send_json(200, {
                "date": run.bundle.date,
                "bookings": [_schedule_item(run, b) for b in run.engine_result.bookings],
            })
        elif path == "/notices":
            self._send_json(200, {
                "date": run.bundle.date,
                "notices": [n.as_dict() for n in run.engine_result.notices],
            })
        elif path == "/settlement":
            self._send_json(200, run.settlement.as_dict(run.bundle.currency))
        elif path == "/decisions":
            self._send_json(200, {
                "date": run.bundle.date,
                "decisions": [d.as_dict() for d in run.engine_result.decisions],
            })
        elif path == "/day_result":
            self._send_json(200, run.as_dict())
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/events":
            self.send_error(404)
            return
        if self.state is None:
            self.send_error(503, "未加载当日数据")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"error": "请求体必须是 JSON 事件对象或事件数组"})
            return
        status, body = self.state.add_events(payload)
        self._send_json(status, body)

    def log_message(self, *_args):
        return


def _schedule_item(run, booking):
    from coordination.model import to_hhmm
    content = run.bundle.contents.get(booking.content, {"name": booking.content})
    res = run.bundle.resources.get(booking.resource, {"name": booking.resource})
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
        "walk_detour": booking.walk_detour,
    }


def run_day_to_files(day_path, out_dir):
    bundle = load_bundle(day_path)
    run = build_deliverables(bundle, out_dir)
    notices = run.engine_result.notices
    decisions = run.engine_result.decisions
    checkins = run.engine_result.valid_checkins()
    total = sum(run.settlement.payee_totals.values())
    print(f"日期 {bundle.date} 回放完成，交付物已写入 {out_dir}/")
    print(f"  排班/现场决策 {len(decisions)} 条；游客通知 {len(notices)} 条；"
          f"有效核销 {len(checkins)} 笔")
    print(f"  重复抑制 {len(run.engine_result.ignored_events)} 条；"
          f"当日分账合计 {total // 100}.{total % 100:02d} {bundle.currency}")
    for name in ("notices", "settlement", "decisions", "schedule", "day_result.json"):
        print(f"  - {out_dir}/{name}{'' if name.endswith('.json') else '.md'}")


def main():
    parser = argparse.ArgumentParser(description=SERVICE_NAME)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--day", default=DEFAULT_DAY, help="一日数据文件路径")
    parser.add_argument("--out", default=DEFAULT_OUT, help="交付物输出目录")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--run-day", action="store_true",
                        help="回放一日数据并生成交付物后退出")
    args = parser.parse_args()
    if args.check:
        assert health_payload()["service"] == SERVICE_ID
        load_bundle(args.day)
        print("基础检查通过")
        return
    if args.run_day:
        run_day_to_files(args.day, args.out)
        return
    Handler.state = DayState(args.day)
    print(f"{SERVICE_NAME} 已加载 {args.day}，监听端口 {args.port}")
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
