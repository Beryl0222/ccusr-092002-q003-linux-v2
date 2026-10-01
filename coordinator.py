"""协调器：资源申报、行程编排、现场事件、账本分账与通知。

- 团队提交行程后，结合步行时间、无障碍需求、道路与户外条件、人员冲突生成可执行路线；
- 迟到、临时改道、取消与离线核销直接改变剩余容量与分账；
- 事件以 event_id 幂等，网络恢复后的重复记录不会再次计费；
- 触碰文化禁忌或缺少授权的项目不会出现在游客方案中；
- 每一次调度成立或被拒绝都写入决策日志，供管理者解释。
"""

from __future__ import annotations

from ledger import Ledger
from model import Request, fmt_hhmm, parse_day, parse_group, parse_hhmm
from planner import ACTIVE, REASON_TEXT, plan_chain, request_view


class CoordError(Exception):
    """协调过程中的业务错误，code 供程序判断，status 供 HTTP 映射。"""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


class Coordinator:
    def __init__(self):
        self.day = None
        self.groups = {}
        self.bookings = []
        self.ledger = Ledger()
        self.notifications = []
        self.decisions = []
        self.events = {}
        self._seq = 0

    # ---- 基础工具 ----

    def _next_id(self, prefix):
        self._seq += 1
        return f"{prefix}-{self._seq:04d}"

    def _make_booking_id(self):
        return self._next_id("B")

    def _require_day(self):
        if self.day is None:
            raise CoordError("no_day", "尚未加载当日资源数据", 409)

    def _decide(self, kind, subject, message, reasons=None):
        entry = {
            "seq": len(self.decisions) + 1,
            "kind": kind,
            "subject": subject,
            "message": message,
            "reasons": list(reasons or []),
        }
        self.decisions.append(entry)
        return entry

    def _notify(self, group_id, kind, message):
        note = {
            "seq": len(self.notifications) + 1,
            "group": group_id,
            "kind": kind,
            "message": message,
        }
        self.notifications.append(note)
        return note

    @staticmethod
    def _reason_texts(reasons):
        return [REASON_TEXT.get(r, r) for r in reasons]

    def _get_group(self, gid):
        group = self.groups.get(gid)
        if group is None:
            raise CoordError("unknown_group", f"未知团队: {gid}", 404)
        return group

    def _find_booking(self, bid):
        if not bid:
            raise CoordError("bad_request", "缺少 booking")
        for booking in self.bookings:
            if booking.id == bid:
                return booking
        raise CoordError("unknown_booking", f"未知安排: {bid}", 404)

    # ---- 当日资源 ----

    def load_day(self, doc):
        """加载当日资源数据（接受完整契约文档或其 sample 部分），并重置当日状态。"""
        sample = doc.get("sample", doc) if isinstance(doc, dict) else doc
        day = parse_day(sample)
        self.__init__()
        self.day = day
        self._decide("day", day.date, f"已加载 {day.date} 当日资源 {len(day.resources)} 项")
        return {"date": day.date, "resources": len(day.resources)}

    def day_view(self):
        self._require_day()
        return {
            "date": self.day.date,
            "resources": [r.to_dict() for r in self.day.resources.values()],
            "conditions": {
                "outdoor_ok": self.day.outdoor_ok,
                "road_closed": sorted(self.day.road_closed),
            },
        }

    # ---- 团队行程 ----

    def submit_group(self, doc):
        """提交团队行程，返回可执行路线与被拒绝的请求（含原因）。"""
        self._require_day()
        group = parse_group(doc)
        if group.id in self.groups:
            raise CoordError("duplicate_group", f"团队 {group.id} 已存在", 409)
        outcomes = plan_chain(
            self.day, group, group.requests, self.bookings, self._make_booking_id
        )
        self.groups[group.id] = group
        accepted, rejected = [], []
        for outcome in outcomes:
            item, booking, reasons = outcome["item"], outcome["booking"], outcome["reasons"]
            if booking is not None:
                resource = self.day.resources[booking.resource_id]
                accepted.append(booking)
                self._decide(
                    "accept", booking.id,
                    f"{group.id} 的「{resource.name}」排在 "
                    f"{fmt_hhmm(booking.start)}-{fmt_hhmm(booking.end)}"
                    f"（步行 {booking.walk_minutes} 分钟，{booking.size} 人）",
                )
            else:
                texts = self._reason_texts(reasons)
                rejected.append({"request": request_view(item), "reasons": reasons,
                                 "messages": texts})
                self._decide(
                    "reject", group.id,
                    f"{group.id} 的「{item.resource}」未排入：{'；'.join(texts)}",
                    reasons,
                )
        if accepted:
            parts = [
                f"{self.day.resources[b.resource_id].name} "
                f"{fmt_hhmm(b.start)}-{fmt_hhmm(b.end)}"
                for b in accepted
            ]
            self._notify(group.id, "scheduled", "已为您排定：" + "；".join(parts))
        for rej in rejected:
            self._notify(
                group.id, "rejected",
                f"「{rej['request']['resource']}」无法安排：{'；'.join(rej['messages'])}",
            )
        return {
            "group": group.id,
            "bookings": [b.to_dict() for b in accepted],
            "rejected": rejected,
        }

    def group_view(self, gid):
        group = self._get_group(gid)
        return {
            "group": {
                "id": group.id,
                "size": group.size,
                "mobility_support": group.mobility_support,
                "arrive": fmt_hhmm(group.arrive),
            },
            "bookings": [b.to_dict() for b in self.bookings if b.group_id == gid],
            "notifications": [n for n in self.notifications if n["group"] == gid],
        }

    # ---- 现场事件 ----

    def apply_event(self, doc):
        """应用一个现场事件。event_id 幂等：重复记录直接确认，不再改变状态或计费。"""
        self._require_day()
        if not isinstance(doc, dict):
            raise CoordError("bad_request", "事件应为对象")
        event_id = doc.get("event_id")
        if not event_id:
            raise CoordError("bad_request", "事件缺少 event_id")
        if event_id in self.events:
            return {
                "event_id": event_id,
                "duplicate": True,
                "message": "重复记录，已忽略，不会再次计费",
            }
        handlers = {
            "checkin": self._on_checkin,
            "late": self._on_late,
            "cancel": self._on_cancel,
            "condition": self._on_condition,
            "reroute": self._on_reroute,
        }
        handler = handlers.get(doc.get("type"))
        if handler is None:
            raise CoordError("bad_request", f"未知事件类型: {doc.get('type')}")
        before = len(self.notifications)
        result = handler(doc)
        result.update({
            "event_id": event_id,
            "type": doc["type"],
            "duplicate": False,
            "notifications": self.notifications[before:],
        })
        self.events[event_id] = {"type": doc["type"], "result": "applied"}
        return result

    def _on_checkin(self, ev):
        """核销：按实际到场人数结算，离线记录与在线记录同等处理。"""
        booking = self._find_booking(ev.get("booking"))
        if booking.status == "checked_in":
            raise CoordError("already_checked_in", "该安排已核销，不能重复计费", 409)
        if booking.status != "scheduled":
            raise CoordError("not_active", f"该安排状态为 {booking.status}，不能核销", 409)
        group = self.groups[booking.group_id]
        actual = ev.get("actual_size", group.size)
        if isinstance(actual, bool) or not isinstance(actual, int) or actual < 1:
            raise CoordError("bad_request", "actual_size 应为正整数")
        booking.status = "checked_in"
        booking.actual_size = actual
        resource = self.day.resources[booking.resource_id]
        entries = self.ledger.bill(
            booking, resource, actual,
            ev.get("time") or fmt_hhmm(booking.start),
            ev["event_id"], offline=bool(ev.get("offline")),
        )
        total = round(resource.price_cny * actual, 2)
        self._notify(
            group.id, "checked_in",
            f"「{resource.name}」已核销 {actual} 人，按实际人数结算 {total:.2f} 元",
        )
        self._decide(
            "accept", booking.id,
            f"核销成立：{group.id} 在「{resource.name}」实际 {actual} 人，合计 {total:.2f} 元",
        )
        return {"booking": booking.to_dict(), "ledger": [e.to_dict() for e in entries]}

    def _on_late(self, ev):
        """迟到：后续未开始的安排整体后移，无法改排的取消并通知。"""
        group = self._get_group(ev.get("group"))
        minutes = ev.get("minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1:
            raise CoordError("bad_request", "minutes 应为正整数")
        future = sorted(
            (b for b in self.bookings if b.group_id == group.id and b.status == "scheduled"),
            key=lambda b: b.start,
        )
        if not future:
            self._decide("event", group.id, f"{group.id} 迟到 {minutes} 分钟，没有可调整的后续安排")
            return {"changed": [], "missed": []}
        cursor = future[0].start + minutes
        earlier = [
            b for b in self.bookings
            if b.group_id == group.id and b.status in ACTIVE and b.end <= future[0].start
        ]
        prev_loc = None
        if earlier:
            last = max(earlier, key=lambda b: b.end)
            prev_loc = self.day.resources[last.resource_id].location
        items = [Request(resource=b.resource_id, content=b.content) for b in future]
        for b in future:
            b.status = "rescheduled"
            b.note = f"迟到{minutes}分钟改排"
        outcomes = plan_chain(
            self.day, group, items, self.bookings, self._make_booking_id,
            cursor=cursor, prev_location=prev_loc,
        )
        changed, missed = [], []
        for old, outcome in zip(future, outcomes):
            name = self.day.resources[old.resource_id].name
            new = outcome["booking"]
            if new is not None:
                changed.append(new.to_dict())
                self._notify(
                    group.id, "changed",
                    f"因团队迟到{minutes}分钟，「{name}」调整为 "
                    f"{fmt_hhmm(new.start)}-{fmt_hhmm(new.end)}",
                )
                self._decide(
                    "accept", new.id,
                    f"迟到改排成立：{group.id} 的「{name}」移至 "
                    f"{fmt_hhmm(new.start)}-{fmt_hhmm(new.end)}",
                )
            else:
                old.status = "missed"
                reasons = outcome["reasons"]
                texts = self._reason_texts(reasons)
                missed.append({"resource": old.resource_id, "reasons": reasons})
                self._notify(
                    group.id, "cancelled",
                    f"因团队迟到{minutes}分钟，「{name}」当日无法改排，已取消（{'；'.join(texts)}）",
                )
                self._decide(
                    "reject", group.id,
                    f"迟到改排失败：{group.id} 的「{name}」无法改排：{'；'.join(texts)}",
                    reasons,
                )
        return {"changed": changed, "missed": missed}

    def _on_cancel(self, ev):
        """取消：释放剩余容量；未核销的安排不产生费用。"""
        group = self._get_group(ev.get("group"))
        if ev.get("booking"):
            targets = [self._find_booking(ev["booking"])]
            if targets[0].group_id != group.id:
                raise CoordError("bad_request", "该安排不属于此团队")
        else:
            targets = [
                b for b in self.bookings
                if b.group_id == group.id and b.status == "scheduled"
            ]
            if not targets:
                rest = [b for b in self.bookings if b.group_id == group.id]
                if any(b.status == "checked_in" for b in rest):
                    raise CoordError(
                        "already_checked_in", "该团队的安排已核销，不能取消", 409
                    )
                raise CoordError("not_active", "该团队没有可取消的安排", 409)
        for b in targets:
            if b.status == "checked_in":
                raise CoordError("already_checked_in", f"{b.id} 已核销，不能取消", 409)
            if b.status != "scheduled":
                raise CoordError("not_active", f"{b.id} 状态为 {b.status}，不能取消", 409)
        cancelled = []
        for b in targets:
            b.status = "cancelled"
            b.note = "团队取消"
            name = self.day.resources[b.resource_id].name
            self._notify(
                group.id, "cancelled",
                f"「{name}」{fmt_hhmm(b.start)}-{fmt_hhmm(b.end)} 已取消，名额已释放",
            )
            self._decide(
                "event", b.id,
                f"{group.id} 取消「{name}」，释放 {b.size} 个名额，未核销不计费",
            )
            cancelled.append(b.to_dict())
        return {"cancelled": cancelled}

    def _on_condition(self, ev):
        """现场条件变化：受影响的户外/道路安排取消并释放容量，通知游客。"""
        if "outdoor_ok" in ev:
            self.day.outdoor_ok = bool(ev["outdoor_ok"])
        road = ev.get("road_closed") or {}
        if not isinstance(road, dict):
            raise CoordError("bad_request", "road_closed 应为对象，含 add/remove 列表")
        for road_id in road.get("add", []):
            self.day.road_closed.add(road_id)
        for road_id in road.get("remove", []):
            self.day.road_closed.discard(road_id)
        cancelled = []
        for b in self.bookings:
            if b.status != "scheduled":
                continue
            resource = self.day.resources[b.resource_id]
            reasons = []
            if resource.outdoor and not self.day.outdoor_ok:
                reasons.append("outdoor_closed")
            if resource.road and resource.road in self.day.road_closed:
                reasons.append("road_closed")
            if not reasons:
                continue
            b.status = "cancelled"
            b.note = "条件变化取消"
            texts = self._reason_texts(reasons)
            self._notify(
                b.group_id, "cancelled",
                f"因现场条件变化，「{resource.name}」{fmt_hhmm(b.start)}-{fmt_hhmm(b.end)} "
                f"取消：{'；'.join(texts)}",
            )
            self._decide(
                "reject", b.id,
                f"条件变化取消 {b.group_id} 的「{resource.name}」：{'；'.join(texts)}",
                reasons,
            )
            cancelled.append(b.to_dict())
        conditions = {
            "outdoor_ok": self.day.outdoor_ok,
            "road_closed": sorted(self.day.road_closed),
        }
        self._decide("event", "conditions", f"现场条件更新：{conditions}")
        return {"conditions": conditions, "cancelled": cancelled}

    def _on_reroute(self, ev):
        """临时改道：放弃部分安排，并在团队当前位置之后补排新的项目。"""

        group = self._get_group(ev.get("group"))
        targets = []
        for bid in ev.get("drop", []):
            booking = self._find_booking(bid)
            if booking.group_id != group.id:
                raise CoordError("bad_request", f"{bid} 不属于 {group.id}")
            if booking.status != "scheduled":
                raise CoordError(
                    "not_active", f"{bid} 状态为 {booking.status}，不能改道", 409
                )
            targets.append(booking)
        adds = []
        for i, item in enumerate(ev.get("add", [])):
            if not isinstance(item, dict) or not item.get("resource") or not item.get("content"):
                raise CoordError("bad_request", f"新增第{i + 1}项缺少 resource 或 content")
            adds.append(Request(
                resource=item["resource"],
                content=item["content"],
                earliest=parse_hhmm(item["earliest"], "earliest") if item.get("earliest") else None,
            ))
        dropped = []
        for b in targets:
            b.status = "cancelled"
            b.note = "临时改道"
            name = self.day.resources[b.resource_id].name
            self._notify(
                group.id, "cancelled",
                f"临时改道：「{name}」{fmt_hhmm(b.start)}-{fmt_hhmm(b.end)} 已取消，名额已释放",
            )
            self._decide("event", b.id, f"{group.id} 临时改道，取消「{name}」")
            dropped.append(b.to_dict())
        remaining = [
            b for b in self.bookings if b.group_id == group.id and b.status in ACTIVE
        ]
        cursor = prev_loc = None
        if remaining:
            last = max(remaining, key=lambda b: b.end)
            cursor = last.end
            prev_loc = self.day.resources[last.resource_id].location
        outcomes = plan_chain(
            self.day, group, adds, self.bookings, self._make_booking_id,
            cursor=cursor, prev_location=prev_loc,
        )
        added, failed = [], []
        for outcome in outcomes:
            item, booking, reasons = outcome["item"], outcome["booking"], outcome["reasons"]
            if booking is not None:
                name = self.day.resources[booking.resource_id].name
                added.append(booking.to_dict())
                self._notify(
                    group.id, "changed",
                    f"临时改道新增「{name}」{fmt_hhmm(booking.start)}-{fmt_hhmm(booking.end)}",
                )
                self._decide(
                    "accept", booking.id,
                    f"临时改道新增：{group.id} 的「{name}」排在 "
                    f"{fmt_hhmm(booking.start)}-{fmt_hhmm(booking.end)}",
                )
            else:
                texts = self._reason_texts(reasons)
                failed.append({"request": request_view(item), "reasons": reasons,
                               "messages": texts})
                self._notify(
                    group.id, "rejected",
                    f"临时改道新增「{item.resource}」无法安排：{'；'.join(texts)}",
                )
                self._decide(
                    "reject", group.id,
                    f"临时改道新增失败：{group.id} 的「{item.resource}」：{'；'.join(texts)}",
                    reasons,
                )
        return {"dropped": dropped, "added": added, "failed": failed}

    # ---- 查询视图 ----

    def capacity_view(self):
        """各资源的剩余容量快照：既有安排与申报的承载量并列，便于核对。"""
        self._require_day()
        resources = []
        for resource in self.day.resources.values():
            active = [
                b for b in self.bookings
                if b.resource_id == resource.id and b.status in ACTIVE
            ]
            resources.append({
                "resource": resource.id,
                "name": resource.name,
                "capacity_groups": resource.capacity_groups,
                "capacity_visitors": resource.capacity_visitors,
                "bookings": [{
                    "booking": b.id,
                    "group": b.group_id,
                    "start": fmt_hhmm(b.start),
                    "end": fmt_hhmm(b.end),
                    "size": b.size,
                    "status": b.status,
                } for b in active],
            })
        return {"date": self.day.date, "resources": resources}

    def decisions_view(self, kind=None):
        return [d for d in self.decisions if kind is None or d["kind"] == kind]

    def notifications_view(self, gid=None):
        return [n for n in self.notifications if gid is None or n["group"] == gid]

    def events_view(self):
        return [{"event_id": k, **v} for k, v in self.events.items()]

    def ledger_view(self, payee=None):
        if payee:
            entries = self.ledger.for_payee(payee)
            return {
                "payee": payee,
                "entries": [e.to_dict() for e in entries],
                "total_cny": round(sum(e.amount for e in entries), 2),
            }
        return {
            "entries": [e.to_dict() for e in self.ledger.entries],
            "totals": self.ledger.totals(),
        }
