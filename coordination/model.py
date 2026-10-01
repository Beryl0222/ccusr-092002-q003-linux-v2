"""时间、步行网络与一日数据的加载校验。

所有金额以整数分（cents）表示，避免浮点误差；时间用 "HH:MM" 并提供
分钟数换算。原始数据只做结构与引用完整性校验，不做任何隐式改写。
"""

import json
from dataclasses import dataclass, field
from pathlib import Path


def to_minutes(hhmm: str) -> int:
    """把 "HH:MM" 转为当日分钟数。"""
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def to_hhmm(minutes: int) -> str:
    """当日分钟数转回 "HH:MM"。"""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def yuan(cents: int) -> str:
    """分转人民币展示字符串，保留两位小数。"""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def overlaps(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    """半开区间 [start, end) 是否重叠。"""
    return start_a < end_b and start_b < end_a


@dataclass(frozen=True)
class Edge:
    a: str
    b: str
    minutes: int
    accessible: bool
    trail: bool = False


@dataclass
class WalkNetwork:
    """地点间步行图；同一次查询内闭包结果缓存。"""

    edges: list[Edge]
    _adj: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dicts(cls, edge_dicts):
        edges = []
        for e in edge_dicts:
            edges.append(
                Edge(
                    a=e["from"],
                    b=e["to"],
                    minutes=int(e["minutes"]),
                    accessible=bool(e.get("accessible", True)),
                    trail=bool(e.get("trail", False)),
                )
            )
        return cls(edges=edges)

    def __post_init__(self):
        adj = {}
        for e in self.edges:
            adj.setdefault(e.a, []).append((e.b, e.minutes, e.accessible))
            adj.setdefault(e.b, []).append((e.a, e.minutes, e.accessible))
        self._adj = adj

    def shortest(self, src, dst, require_accessible=False, blocked_edges=None):
        """Dijkstra 最短路，返回 (分钟, 路径地点列表)；不可达返回 (None, [])。

        blocked_edges: 冻结集合，元素为 frozenset({地点A, 地点B})。
        起终点相同时返回 (0, [src])。
        """
        if src == dst:
            return 0, [src]
        blocked = blocked_edges or frozenset()
        dist = {src: 0}
        prev = {}
        heap = [(0, src)]
        while heap:
            d, node = heap.pop()
            if d != dist.get(node):
                continue
            if node == dst:
                break
            for nxt, weight, accessible in self._adj.get(node, []):
                if require_accessible and not accessible:
                    continue
                if frozenset((node, nxt)) in blocked:
                    continue
                nd = d + weight
                if nd < dist.get(nxt, 10**9):
                    dist[nxt] = nd
                    prev[nxt] = node
                    heap.append((nd, nxt))
                    heap.sort(reverse=True)
        if dst not in dist:
            return None, []
        path = [dst]
        while path[-1] != src:
            path.append(prev[path[-1]])
        path.reverse()
        return dist[dst], path

    def blocked_for_window(self, closures, start_min, end_min):
        """把与查询时段重叠的道路封闭转为冻结边集合。"""
        blocked = set()
        for c in closures:
            if overlaps(start_min, end_min, to_minutes(c["from"]), to_minutes(c["to"])):
                blocked.add(frozenset(c["edge"]))
        return frozenset(blocked)


@dataclass
class DayBundle:
    """visit_day.json 中 sample 的校验后视图，附带索引。"""

    raw: dict
    date: str
    locations: dict
    contents: dict
    resources: dict
    groups: dict
    requests: list
    conditions: dict
    split_rules: dict
    payees: dict
    operations: list
    network: WalkNetwork

    @property
    def currency(self):
        return self.raw.get("pricing_currency", "CNY")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def load_bundle(source) -> DayBundle:
    """从文件路径或已解析字典加载并校验一日数据。"""
    if isinstance(source, (str, Path)):
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        data = source
    sample = data.get("sample")
    _require(isinstance(sample, dict), "缺少 sample 当日数据")

    locations = {l["id"]: l for l in sample.get("locations", [])}
    contents = {c["id"]: c for c in sample.get("contents", [])}
    resources = {r["id"]: r for r in sample.get("resources", [])}
    groups = {g["id"]: g for g in sample.get("groups", [])}

    for res in resources.values():
        _require(res.get("location_id") in locations, f"资源 {res['id']} 引用了未知地点")
        offers = res.get("offers", [])
        _require(offers, f"资源 {res['id']} 未申报任何内容")
        for offer in offers:
            _require(offer["content"] in contents, f"资源 {res['id']} 申报了未知内容 {offer['content']}")
        _require(res.get("sessions"), f"资源 {res['id']} 未申报开放时段")

    for req in sample.get("itinerary_requests", []):
        _require(req["group"] in groups, f"行程引用了未知团队 {req['group']}")
        for item in req.get("items", []):
            res = resources.get(item["resource"])
            _require(res is not None, f"团队 {req['group']} 预约了未知资源 {item['resource']}")
            _require(item["content"] in contents,
                     f"团队 {req['group']} 预约了未知内容 {item['content']}")
            # 资源方未申报同意展示的内容（含禁忌项）不在此处拦截，
            # 交由规划器记录拒绝原因。

    payees = {p["id"]: p for p in sample.get("payees", [])}
    split_rules = sample.get("split_rules", {})
    for rid, rules in split_rules.items():
        _require(rid in resources, f"分账规则引用了未知资源 {rid}")
        total = sum(r["share"] for r in rules)
        _require(abs(total - 1.0) < 1e-9, f"资源 {rid} 分账份额合计 {total}，应为 1.0")
        for r in rules:
            _require(r["payee"] in payees, f"资源 {rid} 的分账收款方 {r['payee']} 未登记")

    network = WalkNetwork.from_dicts(sample.get("edges", []))
    for loc_id in locations:
        _require(
            loc_id in network._adj or len(locations) == 1,
            f"地点 {loc_id} 没有任何步行连接",
        )

    return DayBundle(
        raw=data,
        date=sample.get("date", ""),
        locations=locations,
        contents=contents,
        resources=resources,
        groups=groups,
        requests=sample.get("itinerary_requests", []),
        conditions=sample.get("conditions", {}),
        split_rules=split_rules,
        payees=payees,
        operations=sample.get("operations", []),
        network=network,
    )
