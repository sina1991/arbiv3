from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Token:
    chain: str
    address: str
    symbol: str
    name: str = ""
    decimals: int | None = None


@dataclass(frozen=True)
class PoolCandidate:
    chain: str
    pool_address: str
    dex_id: str
    dex_name: str
    base_token: Token
    quote_token: Token
    liquidity_usd: Decimal
    volume_24h_usd: Decimal
    price_usd: Decimal | None
    url: str | None = None


@dataclass(frozen=True)
class OrderBook:
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    observed_at: float


@dataclass(frozen=True)
class Opportunity:
    route_type: str
    route_id: str
    chain: str
    base_symbol: str
    quote_symbol: str
    budget_usdt: Decimal
    base_amount: Decimal
    quote_proceeds: Decimal
    net_profit_usdt: Decimal
    roi_percent: Decimal
    gas_cost_usdt: Decimal
    liquidity_usd: Decimal
    volume_24h_usd: Decimal
    quote_age_seconds: Decimal
    profitable: bool
    notes: str = ""


@dataclass(frozen=True)
class RankedOpportunity:
    opportunity: Opportunity
    max_profitable_capital_usdt: Decimal
    score: Decimal
    rank: int = 0
