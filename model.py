"""领域模型：当日资源、旅行团队与现场条件的解析与校验。

时间统一用“分钟数”表示（0 为 00:00），对外接口使用 HH:MM 字符串。
"""

from __future__ import annotations

import math
from dataclasses import dataclass


class DayError(ValueError):
    """当日资源数据不符合约定。"""


class GroupError(ValueError):
    """团队行程数据不符合约定。"""


def parse_hhmm(text, field_name="time"):
    """把 HH:MM 解析为分钟数，非法时抛出 DayError。"""
    if not isinstance(text, str) or len(text) != 5 or text[2] != ":":
        raise DayError(f"{field_name} 应为 HH:MM 格式: {text!r}")
    hh, mm = text[:2], text[3:]
    if not (hh.isdigit() and mm.isdigit()):
        raise DayError(f"{field_name} 应为 HH:MM 格式: {text!r}")
    hours, minutes = int(hh), int(mm)
    if hours > 23 or minutes > 59:
        raise DayError(f"{field_name} 超出一天的范围: {text!r}")
    return hours * 60 + minutes


def fmt_hhmm(minutes):
    """把分钟数格式化为 HH:MM。"""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _pos_int(value, field_name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DayError(f"{field_name} 应为正整数: {value!r}")
    return value


@dataclass
class Resource:
    """一个可申报接待的资源（传承人、场馆或户外项目）。"""

    id: str
    kind: str
    name: str
    location: str
    staff: tuple  # 参与接待的传承人/员工，用于冲突检测
    capacity_groups: int  # 同一时段可接待的团队数（样例中的 capacity）
    capacity_visitors: object  # 同一时段总人数上限，None 表示不限
    slot_minutes: int  # 单场时长
    windows: tuple  # 开放时段，元素为 (开始, 结束) 分钟数
    accessible: bool  # 是否满足无障碍需求
    outdoor: bool  # 是否受户外条件影响
    road: object  # 依赖的道路 id，None 表示不依赖
    content: frozenset  # 同意讲述或展示的内容（授权范围）
    taboo: frozenset  # 文化禁忌内容，永不进入游客方案
    price_cny: float  # 每位游客单价
    share: dict  # 分账比例，payee -> 比例，合计必须为 1

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "location": self.location,
            "staff": list(self.staff),
            "capacity_groups": self.capacity_groups,
            "capacity_visitors": self.capacity_visitors,
            "slot_minutes": self.slot_minutes,
            "open": [[fmt_hhmm(s), fmt_hhmm(e)] for s, e in self.windows],
            "accessible": self.accessible,
            "outdoor": self.outdoor,
            "road": self.road,
            "content": sorted(self.content),
            "taboo": sorted(self.taboo),
            "price_cny": self.price_cny,
            "share": dict(self.share),
        }


@dataclass
class Day:
    """一天的资源、步行与现场条件。"""

    date: str
    resources: dict
    walk: dict  # (地点A, 地点B) 排序元组 -> 步行分钟数
    outdoor_ok: bool
    road_closed: set
    default_walk_minutes: int
    mobility_walk_factor: float

    def walk_minutes(self, a, b, mobility=False):
        """两点间步行分钟数；无障碍团队按系数上浮并向上取整。"""
        if a is None or b is None or a == b:
            base = 0
        else:
            key = tuple(sorted((a, b)))
            base = self.walk.get(key, self.default_walk_minutes)
        if mobility and base:
            return math.ceil(base * self.mobility_walk_factor)
        return base


@dataclass
class Request:
    """团队希望体验的一项内容。"""

    resource: str
    content: str
    earliest: object = None  # 最早开始（分钟数）
    latest: object = None  # 最晚开始（分钟数）


@dataclass
class Group:
    """一个旅行团队的当日申报。"""

    id: str
    size: int
    mobility_support: bool
    arrive: int
    requests: list


def _parse_resource(item):
    if not isinstance(item, dict):
        raise DayError("资源条目应为对象")
    rid = item.get("id")
    if not rid:
        raise DayError("资源缺少 id")
    kind = item.get("kind") or "general"
    name = item.get("name") or rid
    location = item.get("location") or rid
    staff = tuple(item.get("staff", []))
    capacity_groups = _pos_int(
        item.get("capacity", item.get("capacity_groups", 1)), f"{rid}.capacity"
    )
    capacity_visitors = item.get("capacity_visitors")
    if capacity_visitors is not None:
        capacity_visitors = _pos_int(capacity_visitors, f"{rid}.capacity_visitors")
    slot_minutes = _pos_int(item.get("slot_minutes", 30), f"{rid}.slot_minutes")
    raw_open = item.get("open")
    if not isinstance(raw_open, list) or not raw_open:
        raise DayError(f"{rid} 缺少 open 开放时段")
    windows = []
    for pair in raw_open:
        start = parse_hhmm(pair[0], f"{rid}.open")
        end = parse_hhmm(pair[1], f"{rid}.open")
        if end <= start:
            raise DayError(f"{rid} 开放时段的结束应晚于开始")
        windows.append((start, end))
    content = frozenset(item.get("content", []))
    taboo = frozenset(item.get("taboo", []))
    overlap = content & taboo
    if overlap:
        raise DayError(f"{rid} 的内容既授权又禁忌: {sorted(overlap)}")
    price = item.get("price_cny", 0)
    if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
        raise DayError(f"{rid} 的 price_cny 应为非负数")
    share = item.get("share") or {"coop": 1.0}
    if not isinstance(share, dict) or not share:
        raise DayError(f"{rid} 的 share 应为非空对象")
    for payee, frac in share.items():
        if isinstance(frac, bool) or not isinstance(frac, (int, float)) or frac < 0:
            raise DayError(f"{rid} 的分账比例非法: {payee}={frac!r}")
    if abs(sum(share.values()) - 1.0) > 1e-6:
        raise DayError(f"{rid} 的分账比例之和应为 1")
    return Resource(
        id=rid,
        kind=kind,
        name=name,
        location=location,
        staff=staff,
        capacity_groups=capacity_groups,
        capacity_visitors=capacity_visitors,
        slot_minutes=slot_minutes,
        windows=tuple(windows),
        accessible=bool(item.get("accessible", True)),
        outdoor=bool(item.get("outdoor", False)),
        road=item.get("road"),
        content=content,
        taboo=taboo,
        price_cny=float(price),
        share=dict(share),
    )


def parse_day(doc):
    """解析并校验当日资源数据（visit_day.json 中 sample 的结构）。"""
    if not isinstance(doc, dict):
        raise DayError("当日数据应为对象")
    date = doc.get("date")
    if not isinstance(date, str) or not date:
        raise DayError("缺少 date")
    default_walk = _pos_int(doc.get("default_walk_minutes", 5), "default_walk_minutes")
    factor = doc.get("mobility_walk_factor", 1.5)
    if isinstance(factor, bool) or not isinstance(factor, (int, float)) or factor < 1:
        raise DayError("mobility_walk_factor 应为不小于 1 的数")
    walk = {}
    for item in doc.get("walk_minutes", []):
        a, b = item.get("from"), item.get("to")
        if not a or not b or a == b:
            raise DayError("walk_minutes 需要不同的 from/to")
        walk[tuple(sorted((a, b)))] = _pos_int(item.get("minutes"), "walk_minutes.minutes")
    items = doc.get("resources")
    if not isinstance(items, list) or not items:
        raise DayError("resources 应为非空列表")
    resources = {}
    for item in items:
        resource = _parse_resource(item)
        if resource.id in resources:
            raise DayError(f"资源 id 重复: {resource.id}")
        resources[resource.id] = resource
    conditions = doc.get("conditions", {})
    if not isinstance(conditions, dict):
        raise DayError("conditions 应为对象")
    return Day(
        date=date,
        resources=resources,
        walk=walk,
        outdoor_ok=bool(conditions.get("outdoor_ok", True)),
        road_closed=set(conditions.get("road_closed", [])),
        default_walk_minutes=default_walk,
        mobility_walk_factor=float(factor),
    )


def parse_group(doc):
    """解析并校验一个团队的行程申报。"""
    if not isinstance(doc, dict):
        raise GroupError("团队数据应为对象")
    gid = doc.get("id")
    if not gid:
        raise GroupError("团队缺少 id")
    size = doc.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise GroupError("size 应为正整数")
    arrive = parse_hhmm(doc.get("arrive", "08:00"), "arrive")
    requests = []
    for i, item in enumerate(doc.get("requests", [])):
        if not isinstance(item, dict):
            raise GroupError(f"第{i + 1}个请求应为对象")
        resource, content = item.get("resource"), item.get("content")
        if not resource or not content:
            raise GroupError(f"第{i + 1}个请求缺少 resource 或 content")
        earliest = parse_hhmm(item["earliest"], "earliest") if item.get("earliest") else None
        latest = parse_hhmm(item["latest"], "latest") if item.get("latest") else None
        if earliest is not None and latest is not None and latest < earliest:
            raise GroupError("latest 早于 earliest")
        requests.append(
            Request(resource=resource, content=content, earliest=earliest, latest=latest)
        )
    return Group(
        id=gid,
        size=size,
        mobility_support=bool(doc.get("mobility_support", False)),
        arrive=arrive,
        requests=requests,
    )
