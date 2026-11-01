from __future__ import annotations

from decimal import Decimal


def to_raw(amount: Decimal, decimals: int) -> int:
    if amount < 0:
        raise ValueError("Token amount cannot be negative")
    return int(amount * (Decimal(10) ** decimals))


def from_raw(amount: int, decimals: int) -> Decimal:
    return Decimal(amount) / (Decimal(10) ** decimals)


# This module intentionally contains only amount conversion helpers.
# V3 market quotes are handled through a configured, verified QuoterV2 adapter
# in market_scanner.py. Router transaction construction and wallet signing
# are not part of this project.
