"""交付物与一日样例的端到端测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from coordination.model import load_bundle
from coordination.reports import (
    build_deliverables, run_day, notices_markdown,
    settlement_markdown, decisions_markdown, final_schedule_markdown,
)

DAY_FILE = Path(__file__).resolve().parent.parent / "contracts" / "visit_day.json"


class SampleDayEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(DAY_FILE)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.day_run = build_deliverables(cls.bundle, cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_sample_day_loads_and_replays(self):
        run = run_day(load_bundle(DAY_FILE))
        self.assertTrue(run.engine_result.bookings)

    def test_taboo_and_unauthorized_absent_from_schedule(self):
        scheduled_contents = {b.content for b in self.day_run.engine_result.bookings}
        self.assertNotIn("sacred-song", scheduled_contents)
        self.assertNotIn("ritual-fragment", scheduled_contents)

    def test_storyteller_conflict_was_reassigned(self):
        reassign = [d for d in self.day_run.plan.decisions if d.code == "resource_reassigned"]
        self.assertTrue(reassign)
        self.assertEqual(reassign[0].group, "G-24")
        g24_mosukun = [b for b in self.day_run.engine_result.bookings
                       if b.group == "G-24" and b.content == "mosukun-frag"]
        self.assertEqual({b.resource for b in g24_mosukun}, {"storyteller-02"})

    def test_accessibility_substitution_for_g18(self):
        # G-18 需轮椅，坡地采药不可达，被室内药材讲解替代
        sub = [d for d in self.day_run.plan.decisions
               if d.group == "G-18" and d.code == "content_substituted"]
        self.assertEqual(len(sub), 1)
        self.assertIn("herb-culture",
                      [b.content for b in self.day_run.engine_result.bookings if b.group == "G-18"])
        self.assertNotIn("herb-walk",
                         [b.content for b in self.day_run.engine_result.bookings if b.group == "G-18"])

    def test_duplicate_offline_checkin_billed_once(self):
        valid = self.day_run.engine_result.valid_checkins()
        folk = [c for c in valid if c.content == "folk-experience"]
        self.assertEqual(len(folk), 1)
        self.assertEqual(folk[0].participants, 20)
        self.assertTrue(folk[0].offline)
        self.assertEqual(
            self.day_run.engine_result.ignored_events,
            [{"event_id": "EV-2004", "reason": "duplicate_event_id"}],
        )

    def test_settlement_uses_actual_headcounts_and_balances(self):
        st = self.day_run.settlement
        # G-18 药材讲解实到 21 人（非申报 22）
        herb = [l for l in st.lines if l.event_id == "EV-2003"][0]
        self.assertEqual(herb.participants, 21)
        self.assertEqual(herb.gross_cents, 21 * 600)
        # 取消的品鉴不计费
        self.assertFalse(any(l.content == "herb-tasting" for l in st.lines))
        # 对账恒等
        self.assertEqual(sum(st.group_totals.values()),
                         sum(st.payee_totals.values()))
        self.assertEqual(sum(st.group_totals.values()), 135200)
        # 逐行配平
        for line in st.lines:
            self.assertEqual(sum(line.splits.values()), line.gross_cents)

    def test_stop_and_reroute_flow(self):
        statuses = {(b.group, b.content): b.status
                    for b in self.day_run.engine_result.bookings}
        self.assertEqual(statuses[("G-24", "birchbark-boat")], "rerouted")
        self.assertIn(("G-24", "birchbark-carving"), statuses)
        kinds = {n.kind for n in self.day_run.engine_result.notices}
        self.assertIn("stop", kinds)
        self.assertIn("reroute", kinds)

    def test_deliverable_files_written(self):
        out = Path(self.tmp.name)
        for name in ("notices.md", "settlement.md", "decisions.md",
                     "schedule.md", "day_result.json"):
            self.assertTrue((out / name).exists(), name)
        data = json.loads((out / "day_result.json").read_text(encoding="utf-8"))
        self.assertEqual(data["date"], "2026-09-16")
        self.assertTrue(data["decisions"])
        self.assertTrue(data["notices"])

    def test_markdown_contains_human_readable_sections(self):
        out = Path(self.tmp.name)
        notices = (out / "notices.md").read_text(encoding="utf-8")
        settlement = (out / "settlement.md").read_text(encoding="utf-8")
        decisions = (out / "decisions.md").read_text(encoding="utf-8")
        self.assertIn("祭祀长歌", notices)
        self.assertIn("1352.00", settlement)
        self.assertIn("EV-2004", settlement)
        self.assertIn("resource_reassigned", decisions)
        self.assertIn("duplicate_suppressed", decisions)


class MarkdownSmokeTest(unittest.TestCase):
    def test_empty_notices_still_renders(self):
        run = run_day(load_bundle(DAY_FILE))
        # 四个渲染函数都应无异常并返回非空文本
        self.assertTrue(notices_markdown(run).startswith("# "))
        self.assertIn("分账", settlement_markdown(run))
        self.assertIn("调度决策", decisions_markdown(run))
        self.assertIn("最终执行排班", final_schedule_markdown(run))


if __name__ == "__main__":
    unittest.main()
