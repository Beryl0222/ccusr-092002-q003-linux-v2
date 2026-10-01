"""民族文旅承载协作台的接待协作服务入口。

- GET  /health                 服务身份
- GET  /day                    当日资源与现场条件
- POST /day                   加载当日资源（重置当日状态）
- POST /groups                团队提交行程，返回可执行路线与拒绝原因
- GET  /groups/{id}           团队的当前方案与变更通知
- POST /events                现场事件（checkin/late/cancel/condition/reroute，event_id 幂等）
- GET  /events                已应用事件
- GET  /bookings              全部安排
- GET  /capacity              各资源剩余容量快照
- GET  /notifications?group=  游客通知
- GET  /ledger[/{payee}]      收益明细与分账汇总
- GET  /decisions?kind=       调度决策日志（accept/reject/event）
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse, parse_qs

from coordinator import CoordError, Coordinator
from model import DayError, GroupError

SERVICE_ID = "heritage-visit-coordination"
SERVICE_NAME = "民族文旅承载协作台"


def health_payload():
    """返回稳定的服务身份信息。"""
    return {"status": "ok", "service": SERVICE_ID, "name": SERVICE_NAME}


class Handler(BaseHTTPRequestHandler):
    """接待协作的 JSON API。"""

    coordinator = Coordinator()

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            raise CoordError("bad_request", "请求体不是合法的 JSON", 400)

    def _handle(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        parts = [unquote(p) for p in path.split("/") if p]
        coord = self.coordinator
        if method == "GET":
            if path == "/health":
                return 200, health_payload()
            if path == "/":
                return 200, {**health_payload(), "endpoints": [
                    "/health", "/day", "/groups", "/groups/{id}", "/events",
                    "/bookings", "/capacity", "/notifications", "/ledger",
                    "/ledger/{payee}", "/decisions",
                ]}
            if path == "/day":
                return 200, coord.day_view()
            if path == "/capacity":
                return 200, coord.capacity_view()
            if path == "/bookings":
                return 200, {"bookings": [b.to_dict() for b in coord.bookings]}
            if path == "/events":
                return 200, {"events": coord.events_view()}
            if path == "/notifications":
                return 200, {"notifications": coord.notifications_view(query.get("group", [None])[0])}
            if path == "/decisions":
                return 200, {"decisions": coord.decisions_view(query.get("kind", [None])[0])}
            if path == "/ledger":
                return 200, coord.ledger_view()
            if len(parts) == 2 and parts[0] == "ledger":
                return 200, coord.ledger_view(parts[1])
            if len(parts) == 2 and parts[0] == "groups":
                return 200, coord.group_view(parts[1])
        else:
            body = self._body()
            if path == "/day":
                return 200, coord.load_day(body)
            if path == "/groups":
                return 201, coord.submit_group(body)
            if path == "/events":
                return 200, coord.apply_event(body)
        return None, None

    def _dispatch(self, method):
        try:
            result = self._handle(method)
        except CoordError as err:
            self._send(err.status, {"error": err.code, "message": str(err)})
            return
        except (DayError, GroupError) as err:
            self._send(400, {"error": "bad_request", "message": str(err)})
            return
        if result[0] is None:
            self._send(404, {"error": "not_found", "message": "未知的接口"})
            return
        self._send(result[0], result[1])

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def log_message(self, *_args):
        return


def run_demo():
    """用 contracts/visit_day.json 走一遍当日流程，打印调度原因与收益明细。"""
    from pathlib import Path

    doc = json.loads(Path("contracts/visit_day.json").read_text(encoding="utf-8"))
    assert doc["service"] == SERVICE_ID
    coord = Coordinator()
    print("== 加载当日资源 ==")
    print(coord.load_day(doc))
    for group in doc["sample"]["groups"]:
        print(f"== 提交团队 {group['id']} ==")
        print(json.dumps(coord.submit_group(group), ensure_ascii=False, indent=2))
    first = coord.bookings[0]
    print(f"== 离线核销 {first.id}（实际 20 人）==")
    print(json.dumps(coord.apply_event({
        "event_id": "demo-checkin-1", "type": "checkin",
        "booking": first.id, "actual_size": 20, "offline": True,
    }), ensure_ascii=False, indent=2))
    print("== 重复上报同一事件（网络恢复后重发）==")
    print(coord.apply_event({"event_id": "demo-checkin-1", "type": "checkin",
                             "booking": first.id, "actual_size": 20}))
    print("== 天气转阴，户外项目暂停 ==")
    print(json.dumps(coord.apply_event({
        "event_id": "demo-cond-1", "type": "condition", "outdoor_ok": False,
    }), ensure_ascii=False, indent=2))
    print("== 调度决策日志 ==")
    for d in coord.decisions:
        print(f"[{d['kind']}] {d['message']}")
    print("== 收益明细 ==")
    print(json.dumps(coord.ledger_view(), ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=SERVICE_NAME)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if args.check:
        assert health_payload()["service"] == SERVICE_ID
        print("基础检查通过")
        return
    if args.demo:
        run_demo()
        return
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
