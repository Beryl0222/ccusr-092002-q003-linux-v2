"""收益结算：只承认成功核销，按实际参与者计费并按申报份额分账。

- 取消、停演、改道前的旧场次没有核销就没有收入；
- 重复事件在引擎阶段已被抑制，这里不会看到同一笔离线核销两次；
- 同一排班即使出现多条核销也只计一次（防御性约束）。
金额全部用整数分；份额产生的余子分按规则分配给集体账户（无集体账户时
分给第一收款方），保证每行分账之和恒等于毛收入。
"""

from dataclasses import dataclass, field


@dataclass
class SettlementLine:
    event_id: str
    group: str
    resource: str
    resource_name: str
    content: str
    content_name: str
    participants: int
    unit_price_cents: int
    gross_cents: int
    splits: dict  # payee_id -> cents
    offline: bool

    def as_dict(self, currency):
        return {
            "event_id": self.event_id,
            "group": self.group,
            "resource": self.resource,
            "resource_name": self.resource_name,
            "content": self.content,
            "content_name": self.content_name,
            "participants": self.participants,
            "unit_price_cents": self.unit_price_cents,
            "gross_cents": self.gross_cents,
            "splits": self.splits,
            "offline": self.offline,
            "currency": currency,
        }


@dataclass
class Settlement:
    lines: list
    payee_totals: dict
    group_totals: dict
    resource_totals: dict
    skipped: list

    def as_dict(self, currency):
        return {
            "currency": currency,
            "lines": [l.as_dict(currency) for l in self.lines],
            "payee_totals": self.payee_totals,
            "group_totals": self.group_totals,
            "resource_totals": self.resource_totals,
            "skipped": self.skipped,
        }


def _split_cents(gross, rules, payee_ids):
    """整数分按份额分配并配平。"""
    alloc = {}
    remainder = gross
    floors = []
    for r in rules:
        amount = gross * r["share"]
        whole = int(amount)  # 向零取整（金额非负即向下取整）
        floors.append((r["payee"], whole))
        remainder -= whole
    # 余子分优先给集体账户，否则给第一收款方
    preferred = "collective" if "collective" in payee_ids else rules[0]["payee"]
    for payee, whole in floors:
        alloc[payee] = alloc.get(payee, 0) + whole
    alloc[preferred] = alloc.get(preferred, 0) + remainder
    assert sum(alloc.values()) == gross
    return alloc


def settle(bundle, engine_result) -> Settlement:
    lines: list[SettlementLine] = []
    payee_totals = {p: 0 for p in bundle.payees}
    group_totals: dict = {}
    resource_totals: dict = {}
    skipped = []
    seen_bookings = set()

    # 以实际发生时间排序，收益明细与现场顺序一致
    checkins = sorted(engine_result.valid_checkins(),
                      key=lambda c: (c.occurred_at, c.event_id))
    for c in checkins:
        if c.booking_key() in seen_bookings:
            skipped.append({"event_id": c.event_id, "reason": "booking_already_settled"})
            continue
        seen_bookings.add(c.booking_key())
        content = bundle.contents[c.content]
        res = bundle.resources[c.resource]
        unit = int(content.get("price_cents", 0))
        gross = unit * c.participants
        rules = bundle.split_rules.get(c.resource)
        if rules is None:
            skipped.append({"event_id": c.event_id,
                            "reason": f"资源 {c.resource} 缺少分账规则，挂起待人工确认"})
            continue
        splits = _split_cents(gross, rules, set(bundle.payees))
        for payee, amount in splits.items():
            payee_totals[payee] += amount
        group_totals[c.group] = group_totals.get(c.group, 0) + gross
        resource_totals[c.resource] = resource_totals.get(c.resource, 0) + gross
        lines.append(SettlementLine(
            event_id=c.event_id,
            group=c.group,
            resource=c.resource,
            resource_name=res["name"],
            content=c.content,
            content_name=content["name"],
            participants=c.participants,
            unit_price_cents=unit,
            gross_cents=gross,
            splits=splits,
            offline=bool(c.offline),
        ))

    # 对账恒等式：毛收入之和 == 各收款方到账之和
    assert sum(group_totals.values()) == sum(payee_totals.values())
    return Settlement(
        lines=lines,
        payee_totals=payee_totals,
        group_totals=group_totals,
        resource_totals=resource_totals,
        skipped=skipped,
    )
