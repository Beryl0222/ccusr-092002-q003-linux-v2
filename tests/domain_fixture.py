"""测试用最小一日数据夹具：室内点 A/B/C、林间户外点 D。"""

import copy

_BASE = {
    "service": "heritage-visit-coordination",
    "sample": {
        "date": "2026-09-16",
        "planning_opened_at": "08:00",
        "locations": [
            {"id": "A", "name": "甲馆", "accessible": True, "indoor": True},
            {"id": "B", "name": "乙坊", "accessible": True, "indoor": True},
            {"id": "C", "name": "丙阁", "accessible": True, "indoor": True},
            {"id": "D", "name": "林间点", "accessible": False, "indoor": False,
             "note": "轮椅无法进入"},
        ],
        "edges": [
            {"from": "A", "to": "B", "minutes": 5, "accessible": True},
            {"from": "A", "to": "C", "minutes": 20, "accessible": True},
            {"from": "B", "to": "C", "minutes": 5, "accessible": True},
            {"from": "A", "to": "D", "minutes": 5, "accessible": False, "trail": True},
            {"from": "B", "to": "D", "minutes": 5, "accessible": False, "trail": True},
        ],
        "contents": [
            {"id": "c1", "name": "常规讲解", "kind": "talk",
             "duration_minutes": 30, "price_cents": 1000},
            {"id": "c2", "name": "内部祭祀歌", "kind": "talk",
             "duration_minutes": 30, "price_cents": 0,
             "taboo_for_visitors": True, "taboo_reason": "族规禁止对外演唱"},
            {"id": "c3", "name": "授权叙事", "kind": "talk",
             "duration_minutes": 30, "price_cents": 1000,
             "requires_authorization": True},
            {"id": "c4", "name": "户外采药", "kind": "herb",
             "duration_minutes": 30, "price_cents": 500,
             "outdoor_only": True, "alternatives": ["c5"]},
            {"id": "c5", "name": "室内药材讲解", "kind": "herb",
             "duration_minutes": 30, "price_cents": 800},
        ],
        "resources": [
            {"id": "r1", "name": "传承人甲", "kind": "talk", "location_id": "A",
             "capacity": 1, "capacity_unit": "team",
             "sessions": [["09:00", "12:00"]],
             "offers": [
                 {"content": "c1", "authorized": True},
                 {"content": "c3", "authorized": False, "note": "未签授权"}]},
            {"id": "r2", "name": "传承人乙", "kind": "talk", "location_id": "B",
             "capacity": 1, "capacity_unit": "team",
             "sessions": [["09:00", "12:00"]],
             "offers": [{"content": "c1", "authorized": True}]},
            {"id": "r3", "name": "大场馆丙", "kind": "talk", "location_id": "C",
             "capacity": 50, "capacity_unit": "person",
             "sessions": [["09:00", "17:00"]],
             "offers": [{"content": "c1", "authorized": True}]},
            {"id": "r4", "name": "户外点丁", "kind": "herb", "location_id": "D",
             "capacity": 1, "capacity_unit": "team",
             "sessions": [["09:00", "12:00"]],
             "offers": [{"content": "c4", "authorized": True}]},
            {"id": "r5", "name": "室内药坊戊", "kind": "herb", "location_id": "B",
             "capacity": 1, "capacity_unit": "team",
             "sessions": [["09:00", "12:00"]],
             "offers": [{"content": "c5", "authorized": True}]},
        ],
        "groups": [
            {"id": "G1", "size": 10, "mobility_support": False, "contact": "领队一"},
            {"id": "G2", "size": 10, "mobility_support": True, "contact": "领队二"},
            {"id": "G3", "size": 30, "mobility_support": False, "contact": "领队三"},
            {"id": "G4", "size": 30, "mobility_support": False, "contact": "领队四"},
        ],
        "itinerary_requests": [],
        "conditions": {
            "weather_forecast": [
                {"period": ["08:00", "18:00"], "summary": "晴", "outdoor_ok": True}],
            "trail_closures": [],
        },
        "pricing_currency": "CNY",
        "payees": [
            {"id": "P1", "name": "甲"},
            {"id": "P2", "name": "乙"},
            {"id": "collective", "name": "联合体"},
        ],
        "split_rules": {
            "r1": [{"payee": "P1", "share": 0.8}, {"payee": "collective", "share": 0.2}],
            "r2": [{"payee": "P2", "share": 0.8}, {"payee": "collective", "share": 0.2}],
            "r3": [{"payee": "P1", "share": 0.8}, {"payee": "collective", "share": 0.2}],
            "r4": [{"payee": "P2", "share": 0.8}, {"payee": "collective", "share": 0.2}],
            "r5": [{"payee": "P2", "share": 0.8}, {"payee": "collective", "share": 0.2}],
        },
        "operations": [],
    },
}


def build_day(requests=None, operations=None, *, weather=True, closures=None,
              sizes=None, resources=None):
    """返回可直接喂给 load_bundle 的深拷贝数据。"""
    data = copy.deepcopy(_BASE)
    sample = data["sample"]
    for gid, size in (sizes or {}).items():
        next(g for g in sample["groups"] if g["id"] == gid)["size"] = size
    if not weather:
        sample["conditions"]["weather_forecast"] = [
            {"period": ["08:00", "18:00"], "summary": "雷雨", "outdoor_ok": False}]
    if closures:
        sample["conditions"]["trail_closures"] = closures
    if resources:
        for rid, patch in resources.items():
            res = next(r for r in sample["resources"] if r["id"] == rid)
            res.update(patch)
    sample["itinerary_requests"] = requests or []
    sample["operations"] = operations or []
    return data


def request(group, items):
    """items: (content, resource, preferred_start) 元组列表。"""
    return {"group": group, "items": [
        {"content": c, "resource": r, "preferred_start": t} for c, r, t in items]}
