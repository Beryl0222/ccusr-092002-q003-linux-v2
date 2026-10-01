"""可执行路线的生成：时段、承载、人员、无障碍与路况约束。

每个排布决定都会给出原因码，管理者可以据此解释“为什么成立”或“为什么被拒绝”。
"""

from __future__ import annotations

from dataclasses import dataclass

from model import fmt_hhmm

ACTIVE = ("scheduled", "checked_in")
SLOT_STEP = 5  # 候选开始时间的步长（分钟）

REASON_TEXT = {
    "unknown_resource": "资源不存在或当日未开放",
    "taboo_content": "内容触及文化禁忌，不进入游客方案",
    "content_not_authorized": "传承人或场馆未授权该内容",
    "not_accessible": "场所无障碍条件不足，无法满足行动不便需求",
    "outdoor_closed": "户外条件不满足，项目暂停",
    "road_closed": "通往该场所的道路封闭",
    "capacity_full": "同时段团队数或人数已满",
    "staff_conflict": "传承人在该时段已被其他团队排班",
    "no_open_slot": "开放时段内没有可容纳的空档",
}


@dataclass
class Booking:
    """一次已排定的接待。"""

    id: str
    group_id: str
    resource_id: str
    content: str
    start: int
    end: int
    size: int
    status: str = "scheduled"  # scheduled / checked_in / cancelled / rescheduled / missed
    actual_size: object = None
    walk_minutes: int = 0
    note: str = ""

    def to_dict(self):
        data = {
            "id": self.id,
            "group": self.group_id,
            "resource": self.resource_id,
            "content": self.content,
            "start": fmt_hhmm(self.start),
            "end": fmt_hhmm(self.end),
            "size": self.size,
            "status": self.status,
            "walk_minutes": self.walk_minutes,
        }
        if self.actual_size is not None:
            data["actual_size"] = self.actual_size
        if self.note:
            data["note"] = self.note
        return data


def request_view(item):
    """把请求渲染为可对外展示的字典。"""
    view = {"resource": item.resource, "content": item.content}
    if item.earliest is not None:
        view["earliest"] = fmt_hhmm(item.earliest)
    if item.latest is not None:
        view["latest"] = fmt_hhmm(item.latest)
    return view


def _overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def _violations(day, resource, size, start, end, bookings):
    """检查候选时段与既有安排的冲突，返回原因码列表（空表示可行）。"""
    codes = []
    groups = 0
    visitors = 0
    for b in bookings:
        if b.status not in ACTIVE:
            continue
        if b.resource_id == resource.id and _overlaps(start, end, b.start, b.end):
            groups += 1
            visitors += b.size
    if groups + 1 > resource.capacity_groups:
        codes.append("capacity_full")
    if resource.capacity_visitors is not None and visitors + size > resource.capacity_visitors:
        if "capacity_full" not in codes:
            codes.append("capacity_full")
    staff = set(resource.staff)
    if staff:
        for b in bookings:
            if b.status not in ACTIVE or b.resource_id == resource.id:
                continue
            other = day.resources.get(b.resource_id)
            if other and staff & set(other.staff) and _overlaps(start, end, b.start, b.end):
                codes.append("staff_conflict")
                break
    return codes


def place(day, group, resource_id, content, cursor, prev_location, bookings,
          earliest=None, latest=None):
    """为团队的一个请求寻找可执行时段。

    返回 (placement, None) 或 (None, reasons)。placement 含资源、起止与步行分钟数；
    reasons 为原因码列表，按出现顺序排列。
    """
    resource = day.resources.get(resource_id)
    if resource is None:
        return None, ["unknown_resource"]
    if content in resource.taboo:
        return None, ["taboo_content"]
    if content not in resource.content:
        return None, ["content_not_authorized"]
    if group.mobility_support and not resource.accessible:
        return None, ["not_accessible"]
    if resource.outdoor and not day.outdoor_ok:
        return None, ["outdoor_closed"]
    if resource.road and resource.road in day.road_closed:
        return None, ["road_closed"]
    walk = day.walk_minutes(prev_location, resource.location, group.mobility_support)
    start_min = cursor + walk
    if earliest is not None:
        start_min = max(start_min, earliest)
    reasons = []
    for win_start, win_end in resource.windows:
        t = max(start_min, win_start)
        remainder = (t - win_start) % SLOT_STEP
        if remainder:
            t += SLOT_STEP - remainder
        while t + resource.slot_minutes <= win_end:
            if latest is not None and t > latest:
                break
            codes = _violations(day, resource, group.size, t, t + resource.slot_minutes, bookings)
            if not codes:
                placement = {
                    "resource": resource,
                    "content": content,
                    "start": t,
                    "end": t + resource.slot_minutes,
                    "walk_minutes": walk,
                }
                return placement, None
            for code in codes:
                if code not in reasons:
                    reasons.append(code)
            t += SLOT_STEP
    if not reasons:
        reasons = ["no_open_slot"]
    return None, reasons


def plan_chain(day, group, items, bookings, make_id, cursor=None, prev_location=None):
    """按顺序排布一组请求，逐项返回结果。

    返回与 items 对齐的结果列表，每项为 {"item", "booking", "reasons"}；
    成功的项会作为 Booking 追加到 bookings。上一项的结束时间与地点决定
    下一项的最早可达时间（含步行）。
    """
    outcomes = []
    cur = group.arrive if cursor is None else cursor
    loc = prev_location
    for item in items:
        placement, reasons = place(
            day, group, item.resource, item.content, cur, loc, bookings,
            earliest=item.earliest, latest=item.latest,
        )
        if placement is None:
            outcomes.append({"item": item, "booking": None, "reasons": reasons})
            continue
        booking = Booking(
            id=make_id(),
            group_id=group.id,
            resource_id=item.resource,
            content=item.content,
            start=placement["start"],
            end=placement["end"],
            size=group.size,
            walk_minutes=placement["walk_minutes"],
        )
        bookings.append(booking)
        outcomes.append({"item": item, "booking": booking, "reasons": None})
        cur = booking.end
        loc = placement["resource"].location
    return outcomes
