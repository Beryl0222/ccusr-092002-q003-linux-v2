"""HTTP 接口的端到端测试：从加载当日资源到核销对账。"""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import service
from coordinator import Coordinator


def call(port, method, path, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode("utf-8"))


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = type("TestHandler", (service.Handler,), {"coordinator": Coordinator()})
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_full_day_flow(self):
        sample = json.loads(Path("contracts/visit_day.json").read_text(encoding="utf-8"))

        status, health = call(self.port, "GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["service"], service.SERVICE_ID)

        status, day = call(self.port, "POST", "/day", sample)
        self.assertEqual(status, 200)
        self.assertEqual(day["resources"], 5)

        group = sample["sample"]["groups"][0]
        status, plan = call(self.port, "POST", "/groups", group)
        self.assertEqual(status, 201)
        self.assertEqual(len(plan["bookings"]), 3)
        booking_id = plan["bookings"][0]["id"]

        # 离线核销，实际 20 人
        status, checked = call(self.port, "POST", "/events", {
            "event_id": "api-e1", "type": "checkin",
            "booking": booking_id, "actual_size": 20, "offline": True,
        })
        self.assertEqual(status, 200)
        self.assertFalse(checked["duplicate"])

        # 网络恢复后重复上报：确认但不重复计费
        status, dup = call(self.port, "POST", "/events", {
            "event_id": "api-e1", "type": "checkin",
            "booking": booking_id, "actual_size": 20,
        })
        self.assertEqual(status, 200)
        self.assertTrue(dup["duplicate"])

        # 游客视角：方案与通知
        status, view = call(self.port, "GET", f"/groups/{group['id']}")
        self.assertEqual(status, 200)
        self.assertTrue(view["notifications"])

        # 村民与场馆视角：收益明细（30 × 20 = 600，传承人 70%）
        status, ledger = call(self.port, "GET", "/ledger/inheritor-w")
        self.assertEqual(status, 200)
        self.assertEqual(ledger["total_cny"], 420.0)

        # 管理者视角：每次调度的成立原因
        status, decisions = call(self.port, "GET", "/decisions?kind=accept")
        self.assertEqual(status, 200)
        self.assertTrue(any("核销成立" in d["message"] for d in decisions["decisions"]))

        # 容量快照
        status, capacity = call(self.port, "GET", "/capacity")
        self.assertEqual(status, 200)
        self.assertEqual(len(capacity["resources"]), 5)

    def test_unknown_path_404(self):
        status, body = call(self.port, "GET", "/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not_found")

    def test_invalid_json_400(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/events",
            data=b"{not json", headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
