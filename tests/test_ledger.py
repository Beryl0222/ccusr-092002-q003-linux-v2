"""收益明细：分账比例、可核对性与取消不计费。"""

import json
import unittest
from pathlib import Path

from coordinator import CoordError, Coordinator


def make_coord():
    coord = Coordinator()
    coord.load_day(json.loads(Path("contracts/visit_day.json").read_text(encoding="utf-8")))
    return coord


def book_and_checkin(coord, resource, content, size, earliest="09:00"):
    coord.submit_group({
        "id": f"G-{resource}", "size": size, "mobility_support": False,
        "arrive": "08:30",
        "requests": [{"resource": resource, "content": content, "earliest": earliest}],
    })
    booking = coord.bookings[-1]
    coord.apply_event({
        "event_id": f"e-{resource}", "type": "checkin",
        "booking": booking.id, "actual_size": size, "offline": True,
    })
    return booking


class LedgerTest(unittest.TestCase):
    def test_split_sums_to_total_and_is_auditable(self):
        """多方分账之和等于总额，每条账目可回链到安排与事件。"""

        coord = make_coord()
        # 林下步道：单价 20，向导 50% / 展馆 30% / 联合体 20%
        book_and_checkin(coord, "herb-02", "herb-field-walk", 7)
        totals = coord.ledger.totals()
        self.assertEqual(totals, {"guide-m": 70.0, "venue-herb": 42.0, "coop": 28.0})
        self.assertEqual(round(sum(totals.values()), 2), 140.0)
        for entry in coord.ledger.entries:
            self.assertTrue(entry.event_id)
            self.assertTrue(entry.booking_id)
            self.assertIn("核销7人", entry.reason)
            self.assertTrue(entry.offline)

    def test_per_payee_statement(self):
        """村民和场馆可以按收款方核对收益明细。"""

        coord = make_coord()
        book_and_checkin(coord, "herb-02", "herb-field-walk", 7)
        book_and_checkin(coord, "storyteller-01", "mosukun-epic", 18)
        view = coord.ledger_view("inheritor-w")
        self.assertEqual(view["total_cny"], 378.0)
        self.assertEqual(len(view["entries"]), 1)
        self.assertEqual(view["entries"][0]["payee"], "inheritor-w")
        venue = coord.ledger_view("venue-herb")
        self.assertEqual(venue["total_cny"], 42.0)

    def test_cancel_before_checkin_bills_nothing(self):
        coord = make_coord()
        coord.submit_group({
            "id": "G-1", "size": 10, "mobility_support": False, "arrive": "08:30",
            "requests": [{"resource": "storyteller-01", "content": "mosukun-epic",
                          "earliest": "09:00"}],
        })
        coord.apply_event({"event_id": "e-x", "type": "cancel", "group": "G-1"})
        self.assertEqual(coord.ledger.entries, [])

    def test_cannot_cancel_after_checkin(self):
        coord = make_coord()
        book_and_checkin(coord, "storyteller-01", "mosukun-epic", 12)
        with self.assertRaises(CoordError) as ctx:
            coord.apply_event({"event_id": "e-y", "type": "cancel", "group": "G-storyteller-01"})
        self.assertEqual(ctx.exception.code, "already_checked_in")


if __name__ == "__main__":
    unittest.main()
