"""现场引擎与分账规则测试。"""

import unittest

from coordination.model import load_bundle, to_minutes
from coordination.planner import plan_requests
from coordination.engine import DayEngine
from coordination.settlement import settle

from domain_fixture import build_day, request


def run(data):
    bundle = load_bundle(data)
    plan = plan_requests(bundle)
    result = DayEngine(bundle, plan).apply()
    return bundle, plan, result


def checkin(eid, at, group, resource, content, start, actual, **kw):
    ev = {"id": eid, "at": at, "type": "checkin", "group": group,
          "resource": resource, "content": content,
          "scheduled_start": start, "actual": actual}
    ev.update(kw)
    return ev


class EngineRulesTest(unittest.TestCase):
    def _one_booking_day(self, operations=None, *, group="G1", start="09:00",
                         resource="r1", content="c1"):
        return build_day(
            [request(group, [(content, resource, start)])],
            operations,
        )

    def test_checkin_billed_by_actual_participants(self):
        data = self._one_booking_day(operations=[
            {"id": "H1", "at": "08:50", "type": "headcount", "group": "G1", "actual": 8},
            checkin("E1", "09:01", "G1", "r1", "c1", "09:00", 8),
        ])
        bundle, _plan, result = run(data)
        st = settle(bundle, result)
        self.assertEqual(len(st.lines), 1)
        self.assertEqual(st.lines[0].participants, 8)
        self.assertEqual(st.lines[0].gross_cents, 8 * 1000)

    def test_duplicate_event_id_bills_once(self):
        ops = [
            checkin("E1", "09:01", "G1", "r1", "c1", "09:00", 10),
            checkin("E1", "09:05", "G1", "r1", "c1", "09:00", 10,
                    offline={"device_id": "tab-1", "recorded_at": "09:01",
                             "synced_at": "09:05", "retry": True}),
        ]
        bundle, _plan, result = run(self._one_booking_day(operations=ops))
        st = settle(bundle, result)
        self.assertEqual(len(st.lines), 1)
        self.assertEqual(sum(st.group_totals.values()), 10 * 1000)
        self.assertEqual(result.ignored_events,
                         [{"event_id": "E1", "reason": "duplicate_event_id"}])

    def test_offline_checkin_recorded_at_occurrence_time(self):
        ops = [checkin("E1", "16:20", "G1", "r1", "c1", "09:00", 9,
                       occurred_at="09:25",
                       offline={"device_id": "tab-9", "recorded_at": "09:25",
                                "synced_at": "16:20"})]
        bundle, _plan, result = run(self._one_booking_day(operations=ops))
        st = settle(bundle, result)
        self.assertEqual(st.lines[0].participants, 9)
        self.assertTrue(st.lines[0].offline)
        self.assertTrue(any(n.kind == "offline_synced" for n in result.notices))

    def test_late_arrival_postpones_and_still_bills(self):
        ops = [
            {"id": "L1", "at": "09:20", "type": "late_arrival", "group": "G1",
             "resource": "r1", "content": "c1", "scheduled_start": "09:00",
             "minutes": 20},
            checkin("E1", "09:22", "G1", "r1", "c1", "09:00", 10),
        ]
        bundle, _plan, result = run(self._one_booking_day(operations=ops))
        b = result.bookings[0]
        self.assertEqual(b.status, "delayed")
        self.assertEqual(b.actual_start, to_minutes("09:20"))
        self.assertTrue(any(n.kind == "late" for n in result.notices))
        st = settle(bundle, result)
        self.assertEqual(len(st.lines), 1)

    def test_cancel_releases_capacity_and_is_free(self):
        # G2（轮椅团队）占用室内 r1 09:00；G1 的户外场停演。G2 先取消
        # 释放 r1，G1 随后改道到同一时段的 r1 应成功；G2 不计费。
        data = build_day(
            [request("G2", [("c1", "r1", "09:00")]),
             request("G1", [("c4", "r4", "09:00")])],
            operations=[
                {"id": "S1", "at": "08:40", "type": "stop", "resource": "r4",
                 "from": "08:40", "reason": "步道结冰"},
                {"id": "X1", "at": "08:55", "type": "cancel", "group": "G2",
                 "resource": "r1", "content": "c1", "scheduled_start": "09:00",
                 "reason": "临时离村", "by": "group_lead"},
                {"id": "R1", "at": "08:57", "type": "reroute", "group": "G1",
                 "from_resource": "r4", "content": "c4", "scheduled_start": "09:00",
                 "to_resource": "r1", "to_content": "c1", "new_start": "09:00",
                 "reason": "户外停演改室内"},
                checkin("E2", "09:02", "G1", "r1", "c1", "09:00", 10),
            ],
        )
        bundle, _plan, result = run(data)
        cancelled = [b for b in result.bookings if b.group == "G2"][0]
        self.assertEqual(cancelled.status, "cancelled")
        g1 = [b for b in result.bookings if b.group == "G1" and b.content == "c1"][0]
        self.assertEqual(g1.resource, "r1")  # 取消释放后改道成功
        self.assertFalse(any(d.code == "reroute_target_full" for d in result.decisions))
        st = settle(bundle, result)
        groups_billed = {l.group for l in st.lines}
        self.assertNotIn("G2", groups_billed)
        self.assertIn("G1", groups_billed)

    def test_stop_then_reroute_bills_new_content_only(self):
        data = build_day(
            [request("G1", [
                ("c4", "r4", "09:00"),
            ])],
            operations=[
                {"id": "S1", "at": "08:40", "type": "stop", "resource": "r4",
                 "from": "08:40", "reason": "步道结冰"},
                {"id": "R1", "at": "08:45", "type": "reroute", "group": "G1",
                 "from_resource": "r4", "content": "c4", "scheduled_start": "09:00",
                 "to_resource": "r5", "to_content": "c5", "new_start": "09:10",
                 "reason": "改室内"},
                checkin("E1", "09:12", "G1", "r5", "c5", "09:10", 10),
            ],
        )
        # 规划阶段 c4 正常排入（晨间天气晴），现场停演后改道 c5
        bundle, _plan, result = run(data)
        old = [b for b in result.bookings if b.content == "c4"]
        new = [b for b in result.bookings if b.content == "c5"]
        self.assertTrue(all(b.status == "rerouted" for b in old))
        self.assertEqual(len(new), 1)
        st = settle(bundle, result)
        self.assertEqual([l.content for l in st.lines], ["c5"])
        self.assertEqual(st.lines[0].unit_price_cents, 800)

    def test_checkin_after_cancel_is_rejected(self):
        ops = [
            {"id": "X1", "at": "08:55", "type": "cancel", "group": "G1",
             "resource": "r1", "content": "c1", "scheduled_start": "09:00",
             "reason": "封村", "by": "village"},
            checkin("E1", "09:01", "G1", "r1", "c1", "09:00", 10),
        ]
        bundle, _plan, result = run(self._one_booking_day(operations=ops))
        st = settle(bundle, result)
        self.assertEqual(st.lines, [])
        self.assertTrue(any(d.code == "checkin_after_cancel" for d in result.decisions))

    def test_reroute_into_full_target_is_rejected(self):
        # r5 09:10 已被 G2 占（团队容量 1），G1 改道过去应失败
        data = build_day(
            [request("G1", [("c4", "r4", "09:00")]),
             request("G2", [("c5", "r5", "09:10")])],
            operations=[
                {"id": "S1", "at": "08:40", "type": "stop", "resource": "r4",
                 "from": "08:40", "reason": "结冰"},
                {"id": "R1", "at": "08:45", "type": "reroute", "group": "G1",
                 "from_resource": "r4", "content": "c4", "scheduled_start": "09:00",
                 "to_resource": "r5", "to_content": "c5", "new_start": "09:10",
                 "reason": "改室内"},
            ],
        )
        bundle, _plan, result = run(data)
        self.assertTrue(any(d.code == "reroute_target_full" for d in result.decisions))

    def test_checkin_after_reroute_against_old_slot_is_rejected(self):
        data = build_day(
            [request("G1", [("c4", "r4", "09:00")])],
            operations=[
                {"id": "S1", "at": "08:40", "type": "stop", "resource": "r4",
                 "from": "08:40", "reason": "结冰"},
                {"id": "R1", "at": "08:45", "type": "reroute", "group": "G1",
                 "from_resource": "r4", "content": "c4", "scheduled_start": "09:00",
                 "to_resource": "r5", "to_content": "c5", "new_start": "09:10",
                 "reason": "改室内"},
                # 旧终端仍按原户外场次核销
                checkin("E-OLD", "09:05", "G1", "r4", "c4", "09:00", 10),
                checkin("E-NEW", "09:12", "G1", "r5", "c5", "09:10", 10),
            ],
        )
        bundle, _plan, result = run(data)
        st = settle(bundle, result)
        self.assertEqual([l.event_id for l in st.lines], ["E-NEW"])
        self.assertTrue(any(d.code == "checkin_after_cancel" for d in result.decisions))

    def test_missing_split_rule_is_suspended_not_dropped_silently(self):
        data = self._one_booking_day(
            operations=[checkin("E1", "09:01", "G1", "r1", "c1", "09:00", 10)])
        bundle = load_bundle(data)
        del bundle.split_rules["r1"]
        plan = plan_requests(bundle)
        result = DayEngine(bundle, plan).apply()
        st = settle(bundle, result)
        self.assertEqual(st.lines, [])
        self.assertTrue(any("缺少分账规则" in s["reason"] for s in st.skipped))


class SettlementRulesTest(unittest.TestCase):
    def test_integer_split_sums_to_gross(self):
        # 1000 分按 0.8/0.2 可整除；再用 1001 分验证余子分配平
        from coordination.settlement import _split_cents
        payees = {"P1": {}, "collective": {}}
        rules = [{"payee": "P1", "share": 0.8}, {"payee": "collective", "share": 0.2}]
        for gross in (1000, 1001, 999, 1):
            alloc = _split_cents(gross, rules, set(payees))
            self.assertEqual(sum(alloc.values()), gross)

    def test_audit_identity_group_equals_payees(self):
        data = build_day(
            [request("G1", [("c1", "r1", "09:00")]),
             request("G2", [("c1", "r2", "10:00")])],
            operations=[
                checkin("E1", "09:01", "G1", "r1", "c1", "09:00", 10),
                checkin("E2", "10:01", "G2", "r2", "c1", "10:00", 7),
            ],
        )
        bundle, _plan, result = run(data)
        st = settle(bundle, result)
        self.assertEqual(sum(st.group_totals.values()),
                         sum(st.payee_totals.values()))
        self.assertEqual(st.resource_totals["r1"], 10_000)
        self.assertEqual(st.resource_totals["r2"], 7_000)


if __name__ == "__main__":
    unittest.main()
