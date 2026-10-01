"""排布约束的单元测试：人员冲突、容量、无障碍、禁忌与授权、路况。"""

import json
import unittest
from pathlib import Path

from coordinator import Coordinator


def load_sample():
    return json.loads(Path("contracts/visit_day.json").read_text(encoding="utf-8"))


def make_coord():
    coord = Coordinator()
    coord.load_day(load_sample())
    return coord


def submit(coord, gid, requests, size=10, mobility=False, arrive="08:30"):
    return coord.submit_group({
        "id": gid,
        "size": size,
        "mobility_support": mobility,
        "arrive": arrive,
        "requests": requests,
    })


class PlanningTest(unittest.TestCase):
    def test_sample_group_chain_with_mobility_walk(self):
        """无障碍团队的连续行程：步行按 1.5 倍取整，时段对齐 5 分钟粒度。"""

        coord = make_coord()
        result = submit(coord, "G-18", [
            {"resource": "storyteller-01", "content": "mosukun-epic", "earliest": "09:00"},
            {"resource": "birch-01", "content": "birch-bark-craft"},
            {"resource": "herb-01", "content": "herb-culture"},
        ], size=22, mobility=True, arrive="08:50")
        self.assertEqual(len(result["bookings"]), 3)
        self.assertEqual(result["rejected"], [])
        self.assertEqual(
            [(b["start"], b["end"]) for b in result["bookings"]],
            [("09:00", "09:40"), ("09:50", "10:40"), ("10:55", "11:25")],
        )
        # 传习所→工坊 5 分钟 × 1.5 向上取整 = 8 分钟；9:48 对齐到 09:50
        self.assertEqual(result["bookings"][1]["walk_minutes"], 8)

    def test_same_inheritor_not_double_booked(self):
        """同一传承人不能同时出现在两个团队的排班中。"""

        coord = make_coord()
        first = submit(coord, "G-A", [
            {"resource": "chant-02", "content": "fireside-talk",
             "earliest": "10:00", "latest": "10:00"},
        ])
        self.assertEqual(first["bookings"][0]["start"], "10:00")
        second = submit(coord, "G-B", [
            {"resource": "storyteller-01", "content": "mosukun-epic",
             "earliest": "10:00", "latest": "10:20"},
        ])
        self.assertEqual(second["bookings"], [])
        self.assertEqual(second["rejected"][0]["reasons"], ["staff_conflict"])

    def test_capacity_full_when_slot_taken(self):
        """容量 1 个团队的资源，同一时段第二个团队被拒。"""

        coord = make_coord()
        submit(coord, "G-1", [
            {"resource": "storyteller-01", "content": "mosukun-epic",
             "earliest": "09:00", "latest": "09:00"},
        ])
        result = submit(coord, "G-2", [
            {"resource": "storyteller-01", "content": "mosukun-epic",
             "earliest": "09:00", "latest": "09:30"},
        ])
        self.assertEqual(result["bookings"], [])
        self.assertEqual(result["rejected"][0]["reasons"], ["capacity_full"])

    def test_visitor_capacity_shifts_next_group(self):
        """人数上限满时，后续团队顺延到下一个可容时段。"""

        coord = make_coord()
        submit(coord, "G-1", [
            {"resource": "birch-01", "content": "birch-bark-craft",
             "earliest": "09:00", "latest": "09:00"},
        ], size=22)
        result = submit(coord, "G-2", [
            {"resource": "birch-01", "content": "birch-bark-craft", "earliest": "09:00"},
        ], size=12)
        # 22 + 12 > 30（容量），顺延到 09:50
        self.assertEqual(result["bookings"][0]["start"], "09:50")

    def test_taboo_content_never_scheduled(self):
        """触碰文化禁忌的内容不进入游客方案。"""

        coord = make_coord()
        result = submit(coord, "G-1", [
            {"resource": "storyteller-01", "content": "shaman-rite-live"},
        ])
        self.assertEqual(result["bookings"], [])
        self.assertEqual(result["rejected"][0]["reasons"], ["taboo_content"])
        self.assertEqual(coord.bookings, [])

    def test_unauthorized_content_rejected(self):
        """未授权讲述的内容不进入游客方案。"""

        coord = make_coord()
        result = submit(coord, "G-1", [
            {"resource": "storyteller-01", "content": "herb-culture"},
        ])
        self.assertEqual(result["rejected"][0]["reasons"], ["content_not_authorized"])

    def test_inaccessible_resource_rejected_for_mobility_group(self):
        """无障碍团队不会被排进无无障碍条件的场所。"""

        coord = make_coord()
        result = submit(coord, "G-1", [
            {"resource": "herb-02", "content": "herb-field-walk"},
        ], mobility=True)
        self.assertEqual(result["rejected"][0]["reasons"], ["not_accessible"])

    def test_outdoor_closed_by_condition(self):
        """户外条件不满足时，户外项目不可排。"""

        coord = make_coord()
        coord.apply_event({"event_id": "e-cond", "type": "condition", "outdoor_ok": False})
        result = submit(coord, "G-1", [
            {"resource": "herb-02", "content": "herb-field-walk"},
        ])
        self.assertEqual(result["rejected"][0]["reasons"], ["outdoor_closed"])

    def test_road_closed_blocks_resource(self):
        """道路封闭时，依赖该道路的项目不可排。"""

        coord = make_coord()
        coord.apply_event({
            "event_id": "e-road", "type": "condition",
            "road_closed": {"add": ["trail-north"]},
        })
        result = submit(coord, "G-1", [
            {"resource": "herb-02", "content": "herb-field-walk"},
        ])
        self.assertEqual(result["rejected"][0]["reasons"], ["road_closed"])

    def test_rejections_are_explained_in_decisions(self):
        """每一次拒绝都能在决策日志中找到具体原因。"""

        coord = make_coord()
        submit(coord, "G-1", [
            {"resource": "storyteller-01", "content": "shaman-rite-live"},
        ])
        rejects = coord.decisions_view("reject")
        self.assertEqual(len(rejects), 1)
        self.assertIn("taboo_content", rejects[0]["reasons"])
        self.assertIn("文化禁忌", rejects[0]["message"])


if __name__ == "__main__":
    unittest.main()
