from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import aiohttp
from web3 import Web3

log = logging.getLogger("arbiV3.scanner")
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search"

ERC20_ABI = [
    {"type": "function", "name": "decimals", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "uint8"}]},
]
V2_POOL_ABI = [
    {"type": "function", "name": "token0", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "address"}]},
    {"type": "function", "name": "token1", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "address"}]},
    {"type": "function", "name": "getReserves", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "uint112"}, {"type": "uint112"}, {"type": "uint32"}]},
]
QUOTER_V2_ABI = [
    {"type": "function", "name": "quoteExactInputSingle", "stateMutability": "nonpayable",
     "inputs": [{"name": "params", "type": "tuple", "components": [
         {"name": "tokenIn", "type": "address"},
         {"name": "tokenOut", "type": "address"},
         {"name": "amountIn", "type": "uint256"},
         {"name": "fee", "type": "uint24"},
         {"name": "sqrtPriceLimitX96", "type": "uint160"}
     ]}],
     "outputs": [
         {"name": "amountOut", "type": "uint256"},
         {"name": "sqrtPriceX96After", "type": "uint160"},
         {"name": "initializedTicksCrossed", "type": "uint32"},
         {"name": "gasEstimate", "type": "uint256"}
     ]}
]


@dataclass(frozen=True)
class MarketQuote:
    market_id: str
    amount_in: Decimal
    amount_out: Decimal
    price_impact_bps: Decimal | None = None


@dataclass(frozen=True)
class RankedOpportunity:
    route: str
    market_a: str
    market_b: str | None
    capital_usdt: Decimal
    gross_profit_usdt: Decimal
    gas_cost_usdt: Decimal | None
    net_profit_usdt: Decimal | None
    roi_percent: Decimal | None
    capacity_usdt: Decimal | None
    score: Decimal
    rank: int = 0


def load_registry(path: str | Path = "market_registry.json") -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported market registry schema_version")
    sizes = [Decimal(value) for value in data.get("capital_sizes_usdt", [])]
    if not sizes or any(value <= 0 for value in sizes) or sizes != sorted(set(sizes)):
        raise ValueError("capital_sizes_usdt must be unique, positive and ascending")
    weights = data.get("ranking_weights", {})
    if set(weights) != {"net_profit", "roi", "capital_capacity"}:
        raise ValueError("ranking_weights must define net_profit, roi and capital_capacity")
    if abs(sum(Decimal(str(v)) for v in weights.values()) - Decimal("1")) > Decimal("0.000001"):
        raise ValueError("ranking_weights must sum to 1")
    return data


def _normalise(values: list[Decimal]) -> list[Decimal]:
    if not values:
        return []
    maximum = max(max(values), Decimal("0"))
    if maximum == 0:
        return [Decimal("0") for _ in values]
    return [max(value, Decimal("0")) / maximum for value in values]


def rank_opportunities(rows: list[dict[str, Any]], weights: dict[str, Any]) -> list[RankedOpportunity]:
    """Rank only fully-costed positive-net opportunities, independently per capital size."""
    weight_profit = Decimal(str(weights["net_profit"]))
    weight_roi = Decimal(str(weights["roi"]))
    weight_capacity = Decimal(str(weights["capital_capacity"]))
    groups: dict[Decimal, list[dict[str, Any]]] = {}
    for row in rows:
        capital = Decimal(str(row["capital_usdt"]))
        net = row.get("net_profit_usdt")
        if net is None or Decimal(str(net)) <= 0:
            continue
        groups.setdefault(capital, []).append(row)

    ranked: list[RankedOpportunity] = []
    for capital, group in groups.items():
        profits = _normalise([Decimal(str(r["net_profit_usdt"])) for r in group])
        rois = _normalise([Decimal(str(r["roi_percent"])) for r in group])
        capacities = _normalise([
            Decimal(str(r.get("capacity_usdt") or 0)) for r in group
        ])
        scored = []
        for row, p, roi, capacity in zip(group, profits, rois, capacities):
            score = (p * weight_profit + roi * weight_roi + capacity * weight_capacity) * 100
            scored.append((score, row))
        scored.sort(key=lambda item: (item[0], Decimal(str(item[1]["net_profit_usdt"]))), reverse=True)
        for index, (score, row) in enumerate(scored, 1):
            ranked.append(RankedOpportunity(
                route=str(row["route"]),
                market_a=str(row["market_a"]),
                market_b=str(row["market_b"]) if row.get("market_b") else None,
                capital_usdt=capital,
                gross_profit_usdt=Decimal(str(row["gross_profit_usdt"])),
                gas_cost_usdt=Decimal(str(row["gas_cost_usdt"])) if row.get("gas_cost_usdt") is not None else None,
                net_profit_usdt=Decimal(str(row["net_profit_usdt"])),
                roi_percent=Decimal(str(row["roi_percent"])),
                capacity_usdt=Decimal(str(row["capacity_usdt"])) if row.get("capacity_usdt") is not None else None,
                score=score.quantize(Decimal("0.01")),
                rank=index,
            ))
    return sorted(ranked, key=lambda item: (item.capital_usdt, item.rank))


class V2PoolQuoter:
    """Read-only constant-product quote adapter for UniswapV2-compatible pools."""

    def __init__(self, web3: Web3, pool_address: str, fee_bps: int):
        if not 0 <= fee_bps < 10_000:
            raise ValueError("V2 fee_bps must be in [0, 9999]")
        self.w3 = web3
        self.pool = web3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=V2_POOL_ABI)
        self.fee_bps = fee_bps
        self.token0 = Web3.to_checksum_address(self.pool.functions.token0().call())
        self.token1 = Web3.to_checksum_address(self.pool.functions.token1().call())
        self.decimals = {
            self.token0: int(web3.eth.contract(address=self.token0, abi=ERC20_ABI).functions.decimals().call()),
            self.token1: int(web3.eth.contract(address=self.token1, abi=ERC20_ABI).functions.decimals().call()),
        }

    def quote_exact_input(self, token_in: str, amount_in: Decimal) -> Decimal:
        token_in = Web3.to_checksum_address(token_in)
        if token_in not in {self.token0, self.token1} or amount_in <= 0:
            raise ValueError("Token is not in pool or amount is not positive")
        reserve0, reserve1, _ = self.pool.functions.getReserves().call()
        reserve_in, reserve_out = (reserve0, reserve1) if token_in == self.token0 else (reserve1, reserve0)
        decimals_in = self.decimals[token_in]
        token_out = self.token1 if token_in == self.token0 else self.token0
        decimals_out = self.decimals[token_out]
        amount_raw = int(amount_in * (Decimal(10) ** decimals_in))
        amount_after_fee = amount_raw * (10_000 - self.fee_bps)
        denominator = reserve_in * 10_000 + amount_after_fee
        if reserve_in <= 0 or reserve_out <= 0 or denominator <= 0:
            raise ValueError("Pool has no usable reserves")
        output_raw = (amount_after_fee * reserve_out) // denominator
        return Decimal(output_raw) / (Decimal(10) ** decimals_out)


class V3QuoterV2:
    """Read-only V3 quote adapter. Requires an independently verified QuoterV2 address."""

    def __init__(self, web3: Web3, quoter_address: str, fee_units: int):
        if not quoter_address or not web3.eth.get_code(Web3.to_checksum_address(quoter_address)):
            raise ValueError("Configured QuoterV2 address has no on-chain code")
        self.w3 = web3
        self.quoter = web3.eth.contract(address=Web3.to_checksum_address(quoter_address), abi=QUOTER_V2_ABI)
        self.fee_units = fee_units

    def quote_exact_input(self, token_in: str, token_out: str, amount_in_raw: int) -> tuple[int, int]:
        params = (Web3.to_checksum_address(token_in), Web3.to_checksum_address(token_out),
                  amount_in_raw, self.fee_units, 0)
        result = self.quoter.functions.quoteExactInputSingle(params).call()
        return int(result[0]), int(result[3])


async def discover_dexscreener_pairs(
    query: str,
    allowed_chain_names: set[str],
    minimum_liquidity_usd: Decimal = Decimal("10000"),
    minimum_volume_24h_usd: Decimal = Decimal("10000"),
) -> list[dict[str, Any]]:
    """Discover candidates only; returned metadata is not treated as verified on-chain truth."""
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(DEXSCREENER_SEARCH, params={"q": query}) as response:
            response.raise_for_status()
            payload = await response.json()
    candidates = []
    for pair in payload.get("pairs") or []:
        liquidity = Decimal(str((pair.get("liquidity") or {}).get("usd") or 0))
        volume = Decimal(str((pair.get("volume") or {}).get("h24") or 0))
        chain = str(pair.get("chainId") or "").lower()
        if chain not in allowed_chain_names or liquidity < minimum_liquidity_usd or volume < minimum_volume_24h_usd:
            continue
        candidates.append({
            "chain": chain,
            "dex": pair.get("dexId"),
            "pair_address": pair.get("pairAddress"),
            "base_token": pair.get("baseToken"),
            "quote_token": pair.get("quoteToken"),
            "price_usd": pair.get("priceUsd"),
            "liquidity_usd": str(liquidity),
            "volume_24h_usd": str(volume),
            "url": pair.get("url"),
            "verification_status": "DISCOVERY_ONLY_REQUIRES_ONCHAIN_VALIDATION",
        })
    return sorted(candidates, key=lambda item: Decimal(item["liquidity_usd"]), reverse=True)


def rpc_for_chain(chain: dict[str, Any]) -> str:
    value = os.getenv(str(chain["rpc_env"]), "").strip()
    if not value:
        raise RuntimeError(f"RPC environment variable {chain['rpc_env']} is not configured")
    return value


def _opportunity_row(route: str, market_a: str, market_b: str | None,
                     capital_usdt: Decimal, gross_profit_usdt: Decimal,
                     gas_cost_usdt: Decimal | None) -> dict[str, Any]:
    net = gross_profit_usdt - gas_cost_usdt if gas_cost_usdt is not None else None
    roi = (net / capital_usdt * 100) if net is not None and capital_usdt > 0 else None
    return {
        "route": route, "market_a": market_a, "market_b": market_b,
        "capital_usdt": capital_usdt, "gross_profit_usdt": gross_profit_usdt,
        "gas_cost_usdt": gas_cost_usdt, "net_profit_usdt": net,
        "roi_percent": roi, "capacity_usdt": None,
    }


def evaluate_dex_cex(
    market_id: str, capital_sizes_usdt: list[Decimal],
    quote_to_base: Any, base_to_quote: Any,
    cex_bid: Decimal, cex_ask: Decimal, cex_fee_rate: Decimal,
    quote_asset_usd_price: Decimal, gas_cost_usdt: Decimal | None,
) -> list[dict[str, Any]]:
    """Evaluate both pre-funded DEX-CEX directions for every quote-currency capital size."""
    if min(cex_bid, cex_ask, quote_asset_usd_price) <= 0 or cex_bid > cex_ask:
        raise ValueError("Invalid CEX prices or quote-asset USD conversion")
    if not 0 <= cex_fee_rate < 1:
        raise ValueError("CEX fee rate must be in [0, 1)")
    rows: list[dict[str, Any]] = []
    for capital_usdt in capital_sizes_usdt:
        capital_quote = capital_usdt / quote_asset_usd_price
        base_from_dex = quote_to_base(capital_quote)
        cex_sell_proceeds_usd = base_from_dex * cex_bid * (1 - cex_fee_rate) * quote_asset_usd_price
        rows.append(_opportunity_row(
            "DEX_CEX", market_id, None, capital_usdt,
            cex_sell_proceeds_usd - capital_usdt, gas_cost_usdt,
        ))

        base_bought_cex = capital_quote / (cex_ask * (1 + cex_fee_rate))
        quote_from_dex = base_to_quote(base_bought_cex)
        dex_sell_proceeds_usd = quote_from_dex * quote_asset_usd_price
        rows.append(_opportunity_row(
            "CEX_DEX", market_id, None, capital_usdt,
            dex_sell_proceeds_usd - capital_usdt, gas_cost_usdt,
        ))

    for row in rows:
        same_route = [candidate for candidate in rows
                      if candidate["route"] == row["route"]
                      and candidate["net_profit_usdt"] is not None
                      and candidate["net_profit_usdt"] > 0]
        if same_route:
            row["capacity_usdt"] = max(candidate["capital_usdt"] for candidate in same_route)
    return rows


def evaluate_dex_dex(
    market_a: str, market_b: str, capital_sizes_usdt: list[Decimal],
    a_quote_to_base: Any, a_base_to_quote: Any,
    b_quote_to_base: Any, b_base_to_quote: Any,
    quote_asset_usd_price: Decimal,
    gas_cost_a_usdt: Decimal | None, gas_cost_b_usdt: Decimal | None,
) -> list[dict[str, Any]]:
    """Compare two pools for the same chain and exact same base/quote token contracts."""
    if quote_asset_usd_price <= 0:
        raise ValueError("quote_asset_usd_price must be positive")
    total_gas = (gas_cost_a_usdt + gas_cost_b_usdt
                 if gas_cost_a_usdt is not None and gas_cost_b_usdt is not None else None)
    rows: list[dict[str, Any]] = []
    for capital_usdt in capital_sizes_usdt:
        capital_quote = capital_usdt / quote_asset_usd_price

        base_from_a = a_quote_to_base(capital_quote)
        quote_from_b = b_base_to_quote(base_from_a)
        rows.append(_opportunity_row(
            "DEX_DEX", market_a, market_b, capital_usdt,
            (quote_from_b - capital_quote) * quote_asset_usd_price, total_gas,
        ))

        base_from_b = b_quote_to_base(capital_quote)
        quote_from_a = a_base_to_quote(base_from_b)
        rows.append(_opportunity_row(
            "DEX_DEX", market_b, market_a, capital_usdt,
            (quote_from_a - capital_quote) * quote_asset_usd_price, total_gas,
        ))

    for row in rows:
        same_direction = [candidate for candidate in rows
                          if candidate["market_a"] == row["market_a"]
                          and candidate["market_b"] == row["market_b"]
                          and candidate["net_profit_usdt"] is not None
                          and candidate["net_profit_usdt"] > 0]
        if same_direction:
            row["capacity_usdt"] = max(candidate["capital_usdt"] for candidate in same_direction)
    return rows
