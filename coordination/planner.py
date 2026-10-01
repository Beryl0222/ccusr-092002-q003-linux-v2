"""行程规划：把团队申报转成可执行排班。

规划顺序固定为行程申报顺序，每一个申报项都留下一条决策记录，说明
成立、替代或拒绝的具体原因。容量与人员冲突在同一份占用账本上判定。
"""

from dataclasses import dataclass, field

from .model import overlaps, to_hhmm, to_minutes

# 决策结果码
ACCEPTED = "accepted"
SUBSTITUTED = "substituted"
REASSIGNED = "reassigned"
REJECTED = "rejected"


@dataclass
class SlotFailure:
    """候选排班失败的结构化原因。"""

    code: str  # window|accessibility|outdoor|unreachable|walk_time|conflict
    detail: str


SUBSTITUTABLE_CODES = {"accessibility", "outdoor", "unreachable"}

START_SEARCH_STEP = 10  # 替代时段搜索步长（分钟）
START_SEARCH_WINDOW = 90  # 相对偏好时间向后搜索的最大分钟数


@dataclass
class Booking:
    """一次占用资源的排班。"""

    booking_id: str
    group: str
    resource: str
    content: str
    start: int
    end: int
    team_size: int
    walk_from: str | None = None  # 上一场地点
    walk_minutes: int | None = None
    walk_path: list = field(default_factory=list)
    walk_detour: bool = False
    # 现场阶段会改写的状态
    status: str = "scheduled"  # scheduled|delayed|rerouted|cancelled|stopped
    actual_start: int | None = None
    actual_end: int | None = None
    note: str = ""

    @property
    def key(self):
        return (self.group, self.resource, self.content, self.start)


@dataclass
class Decision:
    seq: int
    stage: str  # planning | operations
    group: str | None
    code: str
    title: str
    detail: str
    result: str
    request_ref: dict | None = None
    booking_id: str | None = None

    def as_dict(self):
        return {
            "seq": self.seq,
            "stage": self.stage,
            "group": self.group,
            "code": self.code,
            "title": self.title,
            "detail": self.detail,
            "result": self.result,
            "request": self.request_ref,
            "booking_id": self.booking_id,
        }


@dataclass
class Notice:
    at: str
    group: str
    level: str  # info|change|cancel
    title: str
    detail: str
    booking_id: str | None = None
    kind: str = ""

    def as_dict(self):
        return {
            "at": self.at, "group": self.group, "level": self.level,
            "kind": self.kind, "title": self.title, "detail": self.detail,
            "booking_id": self.booking_id,
        }


class Occupancy:
    """按资源维护时间区间占用。"""

    def __init__(self):
        self._intervals = {}  # resource -> [(start,end,units,Booking)]

    def add(self, booking: Booking, units: int):
        self._intervals.setdefault(booking.resource, []).append(
            (booking.start, booking.end, units, booking)
        )

    def remove(self, booking: Booking):
        rows = self._intervals.get(booking.resource, [])
        self._intervals[booking.resource] = [r for r in rows if r[3] is not booking]

    def conflict(self, resource, start, end, units, capacity, capacity_unit):
        """返回与候选区间冲突的现有排班；容量按 team/person 两种口径。"""
        hits = []
        for s, e, u, b in self._intervals.get(resource, []):
            if b.status in ("cancelled", "stopped", "rerouted"):
                continue
            if overlaps(start, end, s, e):
                hits.append(b)
        if capacity_unit == "team":
            # 容量按“同时团队数”计，任何重叠都算冲突（样例中均为 1）。
            if hits:
                return hits
        else:
            busy = sum(u for s, e, u, b in self._intervals.get(resource, [])
                       if b.status not in ("cancelled", "stopped", "rerouted")
                       and overlaps(start, end, s, e))
            if busy + units > capacity:
                return hits
        return []


@dataclass
class PlanResult:
    bookings: list
    decisions: list
    occupancy: Occupancy
    notices: list = field(default_factory=list)

    def booking_by_key(self, group, resource, content, start_hhmm):
        start = start_hhmm if isinstance(start_hhmm, int) else to_minutes(start_hhmm)
        for b in self.bookings:
            if (b.group, b.resource, b.content) == (group, resource, content) and b.start == start:
                return b
        return None


def _offer_map(resource):
    return {o["content"]: o for o in resource.get("offers", [])}


def _content_eligibility(content, offer, group, bundle):
    """返回不满足的授权/禁忌原因列表；空列表表示通过。"""
    reasons = []
    if content.get("taboo_for_visitors"):
        reasons.append(("taboo", f"《{content['name']}》{content.get('taboo_reason', '属于文化禁忌内容')}"))
        return reasons
    if offer is None:
        reasons.append(("not_offered",
                        f"资源方未申报同意向游客讲述或展示《{content['name']}》，不得排入游客方案"))
        return reasons
    if content.get("requires_authorization") and not offer.get("authorized", False):
        reasons.append((
            "not_authorized",
            f"《{content['name']}》需要单独授权，{offer.get('note', '该传承人/场所当前未授权对外讲述或展示')}",
        ))
    if not content.get("requires_authorization") and not offer.get("authorized", False):
        reasons.append(("not_authorized", f"资源方未授权展示《{content['name']}》"))
    return reasons


def _location_accessible(bundle, resource, group):
    loc = bundle.locations[resource["location_id"]]
    return not group.get("mobility_support") or loc.get("accessible", True)


def _session_window(resource, start):
    """资源开放时段中覆盖候选开始时间的那一段 (s,e)，否则 None。"""
    for s, e in resource.get("sessions", []):
        sm, em = to_minutes(s), to_minutes(e)
        if sm <= start < em:
            return sm, em
    return None


def _outdoor_blocked(bundle, start, end):
    """依据申报时天气判断户外是否不可进行；返回说明或 None。"""
    for w in bundle.conditions.get("weather_forecast", []):
        ps, pe = to_minutes(w["period"][0]), to_minutes(w["period"][1])
        if overlaps(start, end, ps, pe) and not w.get("outdoor_ok", True):
            return w.get("summary", "户外条件不适合")
    return None


def plan_requests(bundle):
    occupancy = Occupancy()
    bookings: list[Booking] = []
    decisions: list[Decision] = []
    notices: list[Notice] = []
    seq = 0
    notice_at = bundle.raw.get("sample", {}).get("planning_opened_at", "08:00")

    def notice(group_id, level, kind, title, detail, booking=None):
        notices.append(Notice(
            at=notice_at, group=group_id, level=level, kind=kind,
            title=title, detail=detail,
            booking_id=booking.booking_id if booking else None,
        ))

    def add_decision(group, code, title, detail, result, req=None, booking=None):
        nonlocal seq
        seq += 1
        d = Decision(seq, "planning", group, code, title, detail, result,
                     request_ref=req, booking_id=booking.booking_id if booking else None)
        decisions.append(d)
        return d

    bid = 0

    def new_booking(group_id, res, content_id, start, end, size, walk):
        nonlocal bid
        bid += 1
        wm, path, detour, _reason = walk
        b = Booking(
            booking_id=f"B-{bid:03d}",
            group=group_id,
            resource=res["id"],
            content=content_id,
            start=start,
            end=end,
            team_size=size,
            walk_minutes=wm,
            walk_path=path,
            walk_detour=detour,
            walk_from=path[0] if path else None,
        )
        bookings.append(b)
        occupancy.add(b, 1 if res.get("capacity_unit") == "team" else size)
        return b

    def walk_from_previous(group_id, dst_loc, start):
        """从上一场结束地点计算步行可行性；返回 (分钟,路径,是否绕路,不可达原因)。"""
        prior = [b for b in bookings if b.group == group_id
                 and b.status not in ("cancelled", "stopped", "rerouted")]
        if not prior:
            return 0, [dst_loc], False, None
        prior.sort(key=lambda b: b.end)
        prev = prior[-1]
        require = bool(bundle.groups[group_id].get("mobility_support"))
        src_loc = bundle.resources[prev.resource]["location_id"]
        gap = start - prev.end
        blocked = bundle.network.blocked_for_window(
            bundle.conditions.get("trail_closures", []), prev.end, start + 1
        )
        minutes, path = bundle.network.shortest(src_loc, dst_loc, require, blocked)
        if minutes is None:
            # 再算一次不考虑封闭，确认是封闭导致还是无障碍/无连接导致
            free_minutes, _ = bundle.network.shortest(src_loc, dst_loc, require)
            if free_minutes is not None:
                return None, [], False, SlotFailure(
                    "unreachable", "道路封闭且无可通行绕行路线")
            if require:
                return None, [], False, SlotFailure(
                    "accessibility", "仅有无障碍不支持的步道连接，轮椅无法到达")
            return None, [], False, SlotFailure(
                "unreachable", "两地点间没有可步行路线")
        detour = False
        if blocked:
            free_minutes, _ = bundle.network.shortest(src_loc, dst_loc, require)
            detour = free_minutes is not None and free_minutes < minutes
        if gap < minutes:
            return None, path, detour, SlotFailure(
                "walk_time",
                f"上一场 {to_hhmm(prev.end)} 结束，到该地点步行需 {minutes} 分钟，"
                f"{to_hhmm(start)} 开场前时间不足")
        return minutes, path, detour, None

    def try_slot(group_id, res, content, start, size):
        """检验某资源某开始时刻是否可排班，成功返回 (Booking, None)。"""
        duration = content["duration_minutes"]
        end = start + duration
        window = _session_window(res, start)
        if window is None:
            return None, SlotFailure(
                "window", f"{res['name']}在 {to_hhmm(start)} 不处于申报开放时段内")
        if end > window[1]:
            return None, SlotFailure(
                "window",
                f"{res['name']}该场次 {to_hhmm(window[1])} 结束，{duration} 分钟安排不下")
        if not _location_accessible(bundle, res, bundle.groups[group_id]):
            loc = bundle.locations[res["location_id"]]
            return None, SlotFailure(
                "accessibility",
                f"场地“{loc['name']}”不支持轮椅通行（{loc.get('note', '无障碍不可达')}）")
        if content.get("outdoor_only"):
            weather = _outdoor_blocked(bundle, start, end)
            if weather:
                return None, SlotFailure("outdoor", f"户外项目受天气限制：{weather}")
        walk = walk_from_previous(group_id, res["location_id"], start)
        if walk[3] is not None:
            return None, walk[3]
        clash = occupancy.conflict(
            res["id"], start, end,
            1 if res.get("capacity_unit") == "team" else size,
            res.get("capacity", 1), res.get("capacity_unit", "team"),
        )
        if clash:
            names = "、".join(f"{b.group} {to_hhmm(b.start)}-{to_hhmm(b.end)}" for b in clash)
            unit = "团队" if res.get("capacity_unit") == "team" else "人"
            return None, SlotFailure(
                "conflict",
                f"承载量 {res.get('capacity',1)} {unit} 已被占用（{names}）")
        return new_booking(group_id, res, content["id"], start, end, size, walk), None

    def find_reassignment(group_id, content, preferred_start, size, exclude_resource):
        """同内容改约：其他授权资源的临近时段优先，原资源稍后时段兜底。

        返回 (Booking, resource) 或 (None, None)。候选按
        (是否换人, 与偏好时间的偏差, 开始时间, 资源id) 排序，保证确定。
        """
        candidates = []  # (other_resource_flag, deviation, start, resource_id, res)
        for rid, res in bundle.resources.items():
            offer = _offer_map(res).get(content["id"])
            if offer is None or not offer.get("authorized", False):
                continue
            if rid == exclude_resource:
                times = range(preferred_start + START_SEARCH_STEP,
                              preferred_start + START_SEARCH_WINDOW, START_SEARCH_STEP)
                other_flag = 1
            else:
                times = range(max(0, preferred_start - 20),
                              preferred_start + 31, START_SEARCH_STEP)
                other_flag = 0
            for t in times:
                candidates.append((other_flag, abs(t - preferred_start), t, rid, res))
        candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3]))
        for _flag, _dev, start, _rid, res in candidates:
            booking, _failure = try_slot(group_id, res, content, start, size)
            if booking is not None:
                return booking, res
        return None, None

    for req in bundle.requests:
        group_id = req["group"]
        size = bundle.groups[group_id]["size"]
        for idx, item in enumerate(req.get("items", [])):
            req_ref = {"group": group_id, "index": idx,
                       "content": item["content"], "resource": item["resource"],
                       "preferred_start": item["preferred_start"]}
            res = bundle.resources[item["resource"]]
            content = bundle.contents[item["content"]]
            offer = _offer_map(res).get(item["content"])
            start = to_minutes(item["preferred_start"])

            # 1) 文化禁忌与授权一票否决，不进入改约
            blocks = _content_eligibility(content, offer, bundle.groups[group_id], bundle)
            if blocks:
                code, reason = blocks[0]
                add_decision(group_id, code, f"拒绝排入《{content['name']}》", reason,
                             REJECTED, req_ref)
                notice(group_id, "cancel", code,
                       f"您预约的《{content['name']}》无法安排",
                       f"{reason}。该项目不会出现在您的游览方案中，也不计费用。")
                continue

            # 2) 先尝试原申报资源与时间
            booking, failure = try_slot(group_id, res, content, start, size)

            # 3) 因无障碍/户外/不可达：优先用内容声明的替代项（如坡地采药→室内讲解）
            if booking is None and failure.code in SUBSTITUTABLE_CODES:
                substituted = False
                for alt_id in content.get("alternatives", []):
                    alt = bundle.contents.get(alt_id)
                    if alt is None:
                        continue
                    for alt_res in bundle.resources.values():
                        alt_offer = _offer_map(alt_res).get(alt_id)
                        if alt_offer is None or not alt_offer.get("authorized", False):
                            continue
                        alt_booking, _alt_failure = try_slot(group_id, alt_res, alt, start, size)
                        if alt_booking is not None:
                            add_decision(
                                group_id, "content_substituted",
                                f"《{content['name']}》替换为《{alt['name']}》",
                                f"原项目无法安排：{failure.detail}。已改约至{alt_res['name']} "
                                f"{to_hhmm(alt_booking.start)} 开始的《{alt['name']}》。",
                                SUBSTITUTED, req_ref, alt_booking,
                            )
                            notice(group_id, "change", "content_substituted",
                                   f"《{content['name']}》调整为《{alt['name']}》",
                                   f"原项目无法安排（{failure.detail}）。新安排："
                                   f"{alt_res['name']} {to_hhmm(alt_booking.start)}，"
                                   f"请按新方案前往。",
                                   alt_booking)
                            substituted = True
                            break
                    if substituted:
                        break
                if substituted:
                    continue

            # 4) 其他原因（人员冲突/容量/时间/无替代）尝试同内容改约
            if booking is None:
                alt_booking, alt_res = find_reassignment(group_id, content, start, size, res["id"])
                if alt_booking is not None:
                    walk_note = ""
                    if alt_booking.walk_detour:
                        walk_note = "受道路封闭影响，步行路线已绕行；"
                    add_decision(
                        group_id, "resource_reassigned",
                        f"《{content['name']}》改由 {alt_res['name']} 承接",
                        f"原申报 {res['name']} {item['preferred_start']} 不可安排："
                        f"{failure.detail}。{walk_note}新安排 "
                        f"{to_hhmm(alt_booking.start)} 开始。",
                        REASSIGNED, req_ref, alt_booking,
                    )
                    notice(group_id, "change", "resource_reassigned",
                           f"《{content['name']}》改至 {alt_res['name']}",
                           f"原申报 {res['name']} {item['preferred_start']} 无法安排"
                           f"（{failure.detail}）。{walk_note}新时间 "
                           f"{to_hhmm(alt_booking.start)}，请按新安排前往。",
                           alt_booking)
                    continue
                add_decision(
                    group_id, "no_feasible_slot",
                    f"《{content['name']}》全天无法排入",
                    f"原安排失败原因：{failure.detail}；同日其他资源/时段也无可行排班。",
                    REJECTED, req_ref,
                )
                notice(group_id, "cancel", "no_feasible_slot",
                       f"您预约的《{content['name']}》无法安排",
                       f"{failure.detail}；同日其他资源/时段也无法排入，该项目已从方案中移除，不计费用。")
                continue

            notes = []
            if booking.walk_detour:
                notes.append("受道路封闭影响，步行路线需绕行")
            add_decision(
                group_id, "accepted",
                f"《{content['name']}》排入 {res['name']}",
                f"{to_hhmm(booking.start)}-{to_hhmm(booking.end)} 按申报成立。"
                + ("；".join(notes)),
                ACCEPTED, req_ref, booking,
            )

    return PlanResult(bookings=bookings, decisions=decisions,
                      occupancy=occupancy, notices=notices)
