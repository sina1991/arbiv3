from __future__ import annotations

from decimal import Decimal

from models import Opportunity, RankedOpportunity


def rank_opportunities(rows: list[Opportunity]) -> list[RankedOpportunity]:
    """Rank per tested capital size using profit, capacity proxy and ROI.

    Capacity proxy is the largest tested budget that remains profitable for the
    same route. The score is a transparent 55/30/15 weighted normalization of
    net profit, tested capital capacity and ROI; it is not a probability.
    """
    if not rows:
        return []
    profitable = [row for row in rows if row.profitable and row.net_profit_usdt > 0]
    if not profitable:
        return []

    capacity: dict[str, Decimal] = {}
    for row in profitable:
        capacity[row.route_id] = max(capacity.get(row.route_id, Decimal("0")), row.budget_usdt)

    max_profit = max(row.net_profit_usdt for row in profitable)
    max_capacity = max(capacity.values())
    max_roi = max(row.roi_percent for row in profitable)
    scored: list[tuple[Opportunity, Decimal, Decimal]] = []
    for row in profitable:
        profit_component = row.net_profit_usdt / max_profit if max_profit else Decimal("0")
        capacity_component = capacity[row.route_id] / max_capacity if max_capacity else Decimal("0")
        roi_component = row.roi_percent / max_roi if max_roi else Decimal("0")
        score = (
            profit_component * Decimal("0.55")
            + capacity_component * Decimal("0.30")
            + roi_component * Decimal("0.15")
        ) * 100
        scored.append((row, capacity[row.route_id], score))

    scored.sort(key=lambda item: (item[2], item[0].net_profit_usdt), reverse=True)
    return [
        RankedOpportunity(opportunity=row, max_profitable_capital_usdt=cap, score=score, rank=index)
        for index, (row, cap, score) in enumerate(scored, start=1)
    ]
