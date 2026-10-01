"""现场事件：迟到、取消、条件变化、改道与离线核销的幂等性。"""

import json
import unittest
from pathlib import Path

from coordinator import CoordError, Coordinator


def make_coord():
    coord = Coordinator()
    coord.load_day(json.loads(Path("contracts/visit_day.json").read_text(encoding="utf-8")))
    return coord


def submit(coord, gid, requests, size=10, mobility=False, arrive="08:30"):
    return coord.submit_group({
        "id": gid, "size": size, "mobility_support": mobility,
        "arrive": arrive, "requests": requests,
    })


class CheckinTest(unittest.TestCase):
    def setUp(self):
        self.coord = make_coord()
        submit(self.coord, "G-1", [
            {"resource": "storyteller-01", "content": "mosukun-epic", "earliest": "09:00"},
        ], size=20)
        self.booking = self.coord.bookings[0]

    def test_checkin_bills_actual_size_not_planned(self):
        """收入按实际参与者结算，而不是按申报人数。"""

        result = self.coord.apply_event({
            "event_id": "e-1", "type": "checkin",
            "booking": self.booking.id, "actual_size": 18, "offline": True,
        })
        self.assertFalse(result["duplicate"])
        # 单价 30 × 实际 18 人 = 540；传承人 70%，联合体 30%
        self.assertEqual(self.coord.ledger.totals(),
                         {"inheritor-w": 378.0, "coop": 162.0})
        self.assertEqual(self.booking.actual_size, 18)

    def test_duplicate_event_after_reconnect_not_billed_again(self):
        """网络恢复后重复上报同一 event_id，不会再次计费。"""

        event = {"event_id": "e-1", "type": "checkin",
                 "booking": self.booking.id, "actual_size": 18, "offline": True}
        self.coord.apply_event(event)
        before = len(self.coord.ledger.entries)
        again = self.coord.apply_event(dict(event))
        self.assertTrue(again["duplicate"])
        self.assertEqual(len(self.coord.ledger.entries), before)
        self.assertEqual(self.coord.ledger.totals()["inheritor-w"], 378.0)

    def test_second_checkin_with_new_event_id_rejected(self):
        """换一个 event_id 对同一安排重复核销也会被拒绝，不会重复计费。"""

        self.coord.apply_event({"event_id": "e-1", "type": "checkin",
                                "booking": self.booking.id, "actual_size": 18})
        with self.assertRaises(CoordError) as ctx:
            self.coord.apply_event({"event_id": "e-2", "type": "checkin",
                                    "booking": self.booking.id, "actual_size": 20})
        self.assertEqual(ctx.exception.code, "already_checked_in")
        self.assertEqual(self.coord.ledger.totals()["coop"], 162.0)


class LateTest(unittest.TestCase):
    def test_late_shifts_remaining_chain_and_notifies(self):
        coord = make_coord()
        submit(coord, "G-1", [
            {"resource": "storyteller-01", "content": "mosukun-epic", "earliest": "09:00"},
            {"resource": "herb-01", "content": "herb-culture"},
        ], size=10)
        result = coord.apply_event({
            "event_id": "e-late", "type": "late", "group": "G-1", "minutes": 20,
        })
        self.assertEqual(len(result["changed"]), 2)
        self.assertEqual(result["missed"], [])
        self.assertEqual(
            [(b["start"], b["end"]) for b in result["changed"]],
            [("09:20", "10:00"), ("10:10", "10:40")],
        )
        # 游客收到清晰的变更通知，管理者能查到改排原因
        kinds = [n["kind"] for n in result["notifications"]]
        self.assertEqual(kinds, ["changed", "changed"])
        self.assertTrue(any("迟到20分钟" in n["message"] for n in result["notifications"]))
        accepts = coord.decisions_view("accept")
        self.assertTrue(any("迟到改排成立" in d["message"] for d in accepts))


class CancelTest(unittest.TestCase):
    def test_cancel_releases_capacity_for_others(self):
        coord = make_coord()
        submit(coord, "G-1", [
            {"resource": "storyteller-01", "content": "mosukun-epic",
             "earliest": "09:00", "latest": "09:00"},
        ], size=10)
        blocked = submit(coord, "G-2", [
            {"resource": "storyteller-01", "content": "mosukun-epic",
             "earliest": "09:00", "latest": "09:30"},
        ], size=8)
        self.assertEqual(blocked["rejected"][0]["reasons"], ["capacity_full"])
        coord.apply_event({"event_id": "e-c", "type": "cancel", "group": "G-1"})
        retry = submit(coord, "G-3", [
            {"resource": "storyteller-01", "content": "mosukun-epic",
             "earliest": "09:00", "latest": "09:00"},
        ], size=8)
        self.assertEqual(retry["bookings"][0]["start"], "09:00")
        # 取消未产生任何费用
        self.assertEqual(coord.ledger.totals(), {})


class ConditionTest(unittest.TestCase):
    def test_outdoor_closure_cancels_and_explains(self):
        coord = make_coord()
        submit(coord, "G-1", [
            {"resource": "herb-02", "content": "herb-field-walk", "earliest": "09:00"},
        ], size=10)
        result = coord.apply_event({
            "event_id": "e-cond", "type": "condition", "outdoor_ok": False,
        })
        self.assertEqual(len(result["cancelled"]), 1)
        self.assertEqual(result["cancelled"][0]["status"], "cancelled")
        note = result["notifications"][0]
        self.assertEqual(note["kind"], "cancelled")
        self.assertIn("户外条件不满足", note["message"])
        rejects = coord.decisions_view("reject")
        self.assertIn("outdoor_closed", rejects[-1]["reasons"])


class RerouteTest(unittest.TestCase):
    def test_reroute_drop_and_add(self):
        coord = make_coord()
        submit(coord, "G-1", [
            {"resource": "birch-01", "content": "birch-bark-craft", "earliest": "09:00"},
            {"resource": "herb-01", "content": "herb-culture"},
        ], size=10)
        herb_booking = coord.bookings[1]
        result = coord.apply_event({
            "event_id": "e-r", "type": "reroute", "group": "G-1",
            "drop": [herb_booking.id],
            "add": [{"resource": "herb-02", "content": "herb-field-walk"}],
        })
        self.assertEqual(result["dropped"][0]["status"], "cancelled")
        # 从桦树皮工坊（结束 09:50）步行 9 分钟到步道口，10:00 开场
        self.assertEqual(result["added"][0]["start"], "10:00")
        self.assertEqual(result["added"][0]["resource"], "herb-02")
        self.assertEqual(result["failed"], [])


if __name__ == "__main__":
    unittest.main()
