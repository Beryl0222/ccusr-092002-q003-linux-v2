"""收益明细：按实际参与者结算，按申报比例分账。

只有核销（checkin）才产生账目；取消与未核销的安排不计费。
每条账目都回链到 booking 与事件，村民和场馆可以逐条核对。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LedgerEntry:
    seq: int
    time: str
    booking_id: str
    group_id: str
    resource_id: str
    payee: str
    amount: float
    reason: str
    event_id: str
    offline: bool

    def to_dict(self):
        return {
            "seq": self.seq,
            "time": self.time,
            "booking": self.booking_id,
            "group": self.group_id,
            "resource": self.resource_id,
            "payee": self.payee,
            "amount_cny": self.amount,
            "reason": self.reason,
            "event_id": self.event_id,
            "offline": self.offline,
        }


class Ledger:
    def __init__(self):
        self.entries = []

    def bill(self, booking, resource, actual_size, time_str, event_id, offline=False):
        """按实际人数与申报比例生成账目，返回新增的条目列表。

        分账逐项四舍五入到分，最后一方取差额，保证各方之和等于总额。
        """
        total = round(resource.price_cny * actual_size, 2)
        shares = list(resource.share.items())
        made = []
        running = 0.0
        for i, (payee, frac) in enumerate(shares):
            if i < len(shares) - 1:
                amount = round(total * frac, 2)
                running = round(running + amount, 2)
            else:
                amount = round(total - running, 2)
            entry = LedgerEntry(
                seq=len(self.entries) + 1,
                time=time_str,
                booking_id=booking.id,
                group_id=booking.group_id,
                resource_id=resource.id,
                payee=payee,
                amount=amount,
                reason=f"核销{actual_size}人 × 单价{resource.price_cny:.2f}元 × 比例{frac}",
                event_id=event_id,
                offline=offline,
            )
            self.entries.append(entry)
            made.append(entry)
        return made

    def totals(self):
        totals = {}
        for entry in self.entries:
            totals[entry.payee] = round(totals.get(entry.payee, 0.0) + entry.amount, 2)
        return totals

    def for_payee(self, payee):
        return [entry for entry in self.entries if entry.payee == payee]
