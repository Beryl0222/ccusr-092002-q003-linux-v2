"""规划器规则测试。"""

import unittest

from coordination.model import load_bundle, to_minutes
from coordination.planner import plan_requests, ACCEPTED, SUBSTITUTED, REASSIGNED, REJECTED

from domain_fixture import build_day, request


def codes(plan):
    return [(d.group, d.code, d.result) for d in plan.decisions]


class PlanningRulesTest(unittest.TestCase):
    def test_taboo_content_never_enters_plan(self):
        data = build_day([request("G1", [("c2", "r1", "09:00")])])
        plan = plan_requests(load_bundle(data))
        self.assertEqual(plan.bookings, [])
        d = plan.decisions[0]
        self.assertEqual((d.code, d.result), ("taboo", REJECTED))
        self.assertIn("族规", d.detail)
        # 同时产生面向游客的移除通知
        self.assertTrue(any(n.kind == "taboo" for n in plan.notices))

    def test_unauthorized_content_rejected(self):
        data = build_day([request("G1", [("c3", "r1", "09:00")])])
        plan = plan_requests(load_bundle(data))
        self.assertEqual(plan.bookings, [])
        self.assertEqual(plan.decisions[0].code, "not_authorized")

    def test_not_offered_content_rejected(self):
        # r2 的申报清单里没有 c3；即使 c3 本身存在也不得排入
        data = build_day([request("G1", [("c3", "r2", "09:00")])])
        plan = plan_requests(load_bundle(data))
        self.assertEqual(plan.decisions[0].code, "not_offered")

    def test_same_storyteller_double_booking_is_reassigned(self):
        data = build_day([
            request("G1", [("c1", "r1", "09:00")]),
            request("G2", [("c1", "r1", "09:00")]),
        ])
        plan = plan_requests(load_bundle(data))
        by_group = {}
        for d in plan.decisions:
            by_group.setdefault(d.group, []).append(d)
        self.assertEqual(by_group["G1"][0].result, ACCEPTED)
        g2 = by_group["G2"][0]
        self.assertEqual((g2.code, g2.result), ("resource_reassigned", REASSIGNED))
        # 改约落到另一位传承人 r2
        booking = next(b for b in plan.bookings if b.group == "G2")
        self.assertEqual(booking.resource, "r2")

    def test_person_capacity_allows_overlap_within_limit(self):
        data = build_day(
            [request("G1", [("c1", "r3", "09:00")]),
             request("G3", [("c1", "r3", "09:00")])],
            sizes={"G1": 20, "G3": 30},
        )
        plan = plan_requests(load_bundle(data))
        self.assertTrue(all(d.result == ACCEPTED for d in plan.decisions))

    def test_person_capacity_exceeded_is_reassigned_or_rejected(self):
        data = build_day(
            [request("G3", [("c1", "r3", "09:00")]),
             request("G4", [("c1", "r3", "09:00")])],
            sizes={"G3": 30, "G4": 30},
        )
        plan = plan_requests(load_bundle(data))
        second = [d for d in plan.decisions if d.group == "G4"][0]
        self.assertIn(second.result, (REASSIGNED, REJECTED))

    def test_walk_time_insufficient_pushes_start(self):
        # 09:00-09:30 在 A；r1 09:30 即关门无法连排，下一场无论落到 B(5 分钟)
        # 还是 C(20 分钟)，新开始时间都必须留足从上一场地点出发的步行时间
        data = build_day([request("G1", [
            ("c1", "r1", "09:00"),
            ("c1", "r3", "09:32"),
        ])], resources={"r1": {"sessions": [["09:00", "09:30"]]}})
        plan = plan_requests(load_bundle(data))
        first_end = to_minutes("09:30")
        following = [b for b in plan.bookings if b.booking_id != "B-001"]
        self.assertEqual(len(following), 1)
        b = following[0]
        self.assertIsNotNone(b.walk_minutes)
        self.assertGreater(b.walk_minutes, 0)
        self.assertGreaterEqual(b.start - first_end, b.walk_minutes)

    def test_accessibility_blocks_trail_and_substitutes_indoor(self):
        # G2 需轮椅：户外点 D 不可达，c4 声明替代 c5（在 B，且 A->B 仅 5 分钟）
        data = build_day([request("G2", [
            ("c1", "r1", "09:00"),
            ("c4", "r4", "10:00"),
        ])])
        plan = plan_requests(load_bundle(data))
        books = {b.content for b in plan.bookings}
        self.assertNotIn("c4", books)
        self.assertIn("c5", books)
        sub = [d for d in plan.decisions if d.code == "content_substituted"][0]
        self.assertEqual(sub.result, SUBSTITUTED)
        self.assertIn("轮椅", sub.detail)

    def test_non_accessible_group_can_reach_trail(self):
        data = build_day([request("G1", [("c4", "r4", "09:00")])])
        plan = plan_requests(load_bundle(data))
        self.assertEqual(plan.decisions[0].result, ACCEPTED)

    def test_bad_weather_rejects_outdoor_without_indoor_followup(self):
        # r4 提供户外 c4，天气不适合；G1 无轮椅，可达性不触发替代分支，
        # 但天气原因同样应走替代/拒绝，绝不排入户外
        data = build_day([request("G1", [("c4", "r4", "09:00")])], weather=False)
        plan = plan_requests(load_bundle(data))
        books = [b for b in plan.bookings if b.content == "c4"]
        self.assertEqual(books, [])

    def test_trail_closure_blocks_between_stops(self):
        # 首场在 A，之后要去 D；A-D、B-D 同时封闭则 D 不可达，
        # 走声明替代 c5（室内 B）
        data = build_day(
            [request("G1", [
                ("c1", "r1", "09:00"),
                ("c4", "r4", "10:00"),
            ])],
            closures=[{"edge": ["A", "D"], "from": "08:00", "to": "12:00",
                       "reason": "施工"},
                      {"edge": ["B", "D"], "from": "08:00", "to": "12:00",
                       "reason": "施工"}],
        )
        plan = plan_requests(load_bundle(data))
        books = {b.content for b in plan.bookings}
        self.assertNotIn("c4", books)
        self.assertIn("c5", books)

    def test_walk_path_uses_previous_stop(self):
        # 首场在 B，之后去 D；B-D 是 5 分钟步道，应被采用且步行核算生效
        data = build_day([request("G1", [
            ("c1", "r2", "09:00"),
            ("c4", "r4", "09:40"),
        ])])
        plan = plan_requests(load_bundle(data))
        outdoor = [b for b in plan.bookings if b.content == "c4"][0]
        self.assertEqual(outdoor.walk_minutes, 5)
        self.assertEqual(outdoor.walk_path[0], "B")

    def test_session_window_enforced(self):
        data = build_day([request("G1", [("c1", "r1", "11:45")])])
        plan = plan_requests(load_bundle(data))
        # r1 12:00 关门，30 分钟安排不下；可改约到 r2/r3，否则拒绝
        r1_books = [b for b in plan.bookings if b.resource == "r1"]
        self.assertEqual(r1_books, [])


if __name__ == "__main__":
    unittest.main()
