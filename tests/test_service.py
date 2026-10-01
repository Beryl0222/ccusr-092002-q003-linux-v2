"""HTTP 端点冒烟与事件幂等测试。"""

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from http.client import HTTPConnection

import service
from service import Handler, DayState


class ServerHarness:
    def __init__(self, day_path="contracts/visit_day.json"):
        Handler.state = DayState(day_path)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    def get(self, path):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, body

    def post(self, path, payload):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", path, json.dumps(payload),
                     {"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, body


class HttpApiTest(unittest.TestCase):
    def test_health(self):
        with ServerHarness() as srv:
            status, body = srv.get("/health")
            self.assertEqual(status, 200)
            self.assertEqual(body["service"], service.SERVICE_ID)

    def test_read_endpoints(self):
        with ServerHarness() as srv:
            for path in ("/plan", "/schedule", "/notices",
                         "/settlement", "/decisions", "/day_result"):
                status, body = srv.get(path)
                self.assertEqual(status, 200, path)
            status, decisions = srv.get("/decisions")
            codes = {d["code"] for d in decisions["decisions"]}
            self.assertIn("taboo", codes)
            self.assertIn("duplicate_suppressed", codes)

    def test_404(self):
        with ServerHarness() as srv:
            conn = HTTPConnection("127.0.0.1", srv.port, timeout=5)
            conn.request("GET", "/nope")
            self.assertEqual(conn.getresponse().status, 404)
            conn.close()

    def test_post_duplicate_event_is_idempotent(self):
        with ServerHarness() as srv:
            payload = {
                "id": "EV-2004", "at": "17:00", "type": "checkin",
                "group": "G-18", "resource": "folk-01",
                "content": "folk-experience", "scheduled_start": "15:30",
                "actual": 20,
            }
            status, body = srv.post("/events", payload)
            self.assertEqual(status, 200)
            self.assertEqual(body["accepted_event_ids"], [])
            self.assertEqual(body["duplicate_event_ids"], ["EV-2004"])
            # 总额不因重复上报而变化
            _, settlement = srv.get("/settlement")
            self.assertEqual(sum(settlement["payee_totals"].values()), 135200)

    def test_post_new_event_changes_state(self):
        with ServerHarness() as srv:
            _, before = srv.get("/settlement")
            payload = {
                "id": "EV-TEST-1", "at": "09:30", "type": "checkin",
                "group": "G-18", "resource": "storyteller-01",
                "content": "mosukun-frag", "scheduled_start": "09:00",
                "actual": 5,
            }
            status, body = srv.post("/events", payload)
            self.assertEqual(status, 200)
            self.assertEqual(body["accepted_event_ids"], ["EV-TEST-1"])
            _, after = srv.get("/settlement")
            # 同一排班 EV-2001 已结算，防御性去重使总额不变
            self.assertEqual(sum(after["payee_totals"].values()),
                             sum(before["payee_totals"].values()))

    def test_post_bad_json(self):
        with ServerHarness() as srv:
            conn = HTTPConnection("127.0.0.1", srv.port, timeout=5)
            conn.request("POST", "/events", "not-json",
                         {"Content-Type": "application/json"})
            self.assertEqual(conn.getresponse().status, 400)
            conn.close()


if __name__ == "__main__":
    unittest.main()
