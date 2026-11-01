from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable

import aiohttp
from web3 import Web3

log = logging.getLogger("arbiV3.scanner")
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search"
CAPITAL_SIZES_USDT = tuple(Decimal(x) for x in ("100", "250", "500", "1000", "2500", "5000"))

ERC20_ABI = [
    {"type":"function","name":"decimals","stateMutability":"view","inputs":[],"outputs":[{"type":"uint8"}]},
    {"type":"function","name":"symbol","stateMutability":"view","inputs":[],"outputs":[{"type":"string"}]},
]
FACTORY_ABI = [
    {"type":"function","name":"getPool","stateMutability":"view",
     "inputs":[{"name":"tokenA","type":"address"},{"name":"tokenB","type":"address"},{"name":"fee","type":"uint24"}],
     "outputs":[{"name":"pool","type":"address"}]},
    {"type":"function","name":"feeAmountTickSpacing","stateMutability":"view",
     "inputs":[{"name":"fee","type":"uint24"}],"outputs":[{"name":"tickSpacing","type":"int24"}]}
]
POOL_ABI = [
    {"type":"function","name":"factory","stateMutability":"view","inputs":[],"outputs":[{"type":"address"}]},
    {"type":"function","name":"token0","stateMutability":"view","inputs":[],"outputs":[{"type":"address"}]},
    {"type":"function","name":"token1","stateMutability":"view","inputs":[],"outputs":[{"type":"address"}]},
    {"type":"function","name":"fee","stateMutability":"view","inputs":[],"outputs":[{"type":"uint24"}]},
    {"type":"function","name":"tickSpacing","stateMutability":"view","inputs":[],"outputs":[{"type":"int24"}]},
    {"type":"function","name":"liquidity","stateMutability":"view","inputs":[],"outputs":[{"type":"uint128"}]},
    {"type":"function","name":"slot0","stateMutability":"view","inputs":[],"outputs":[
        {"type":"uint160"},{"type":"int24"},{"type":"uint16"},{"type":"uint16"},
        {"type":"uint16"},{"type":"uint8"},{"type":"bool"}]},
]
QUOTER_V2_ABI = [
    {"type":"function","name":"quoteExactInputSingle","stateMutability":"nonpayable",
     "inputs":[{"name":"params","type":"tuple","components":[
         {"name":"tokenIn","type":"address"},{"name":"tokenOut","type":"address"},
         {"name":"amountIn","type":"uint256"},{"name":"fee","type":"uint24"},
         {"name":"sqrtPriceLimitX96","type":"uint160"}]}],
     "outputs":[{"name":"amountOut","type":"uint256"},{"name":"sqrtPriceX96After","type":"uint160"},
                {"name":"initializedTicksCrossed","type":"uint32"},{"name":"gasEstimate","type":"uint256"}]},
]
POOL_ABI_UINT32_FEE_PROTOCOL = json.loads(json.dumps(POOL_ABI))
POOL_ABI_UINT32_FEE_PROTOCOL[-1]["outputs"][5]["type"] = "uint32"


def _address(value: str) -> str:
    if not Web3.is_address(value):
        raise ValueError(f"Invalid EVM address: {value}")
    return Web3.to_checksum_address(value)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def to_raw(amount: Decimal, decimals: int) -> int:
    if decimals < 0 or decimals > 255 or amount < 0:
        raise ValueError("Invalid token amount or decimals")
    scaled = amount * (Decimal(10) ** decimals)
    if scaled != scaled.to_integral_value():
        raise ValueError("Amount has more precision than token decimals")
    return int(scaled)


def from_raw(amount: int, decimals: int) -> Decimal:
    return Decimal(amount) / (Decimal(10) ** decimals)


@dataclass(frozen=True)
class V3PoolSnapshot:
    chain_id: int
    factory: str
    pool: str
    token0: str
    token1: str
    token0_decimals: int
    token1_decimals: int
    fee: int
    tick_spacing: int
    sqrt_price_x96: int
    tick: int
    liquidity: int
    unlocked: bool
    block_number: int
    timestamp_utc: str
    status: str = "VERIFIED"


@dataclass(frozen=True)
class V3Quote:
    token_in: str
    token_out: str
    amount_in: Decimal
    amount_out: Decimal
    effective_price: Decimal
    spot_price: Decimal
    price_impact_bps: Decimal
    initialized_ticks_crossed: int
    gas_estimate: int
    block_number: int
    timestamp_utc: str
    fee_units: int
    tick_before: int
    sqrt_price_x96_after: int


class V3PoolValidator:
    """Validate chain, deployed factory, factory mapping, pool state and ERC-20 metadata."""

    def __init__(self, web3: Web3):
        self.w3 = web3

    def validate(self, *, expected_chain_id: int, factory_address: str,
                 pool_address: str, token_a: str, token_b: str, fee: int,
                 slot0_fee_protocol_type: str = "uint8") -> V3PoolSnapshot:
        if int(self.w3.eth.chain_id) != int(expected_chain_id):
            raise ValueError(f"Chain ID mismatch: expected {expected_chain_id}, got {self.w3.eth.chain_id}")
        if not 0 <= int(fee) < 1_000_000:
            raise ValueError("V3 fee must be an integer in [0, 999999]")
        factory_address, pool_address = _address(factory_address), _address(pool_address)
        token_a, token_b = _address(token_a), _address(token_b)
        if token_a.lower() == token_b.lower():
            raise ValueError("Pool tokens must be different")
        for label, address in (("factory", factory_address), ("pool", pool_address),
                               ("tokenA", token_a), ("tokenB", token_b)):
            if not self.w3.eth.get_code(address):
                raise ValueError(f"{label} has no deployed bytecode: {address}")
        if slot0_fee_protocol_type not in {"uint8","uint32"}:
            raise ValueError("Unsupported slot0 feeProtocol ABI type")
        factory = self.w3.eth.contract(address=factory_address, abi=FACTORY_ABI)
        pool_abi = POOL_ABI_UINT32_FEE_PROTOCOL if slot0_fee_protocol_type == "uint32" else POOL_ABI
        pool = self.w3.eth.contract(address=pool_address, abi=pool_abi)
        factory_reported = _address(factory.functions.getPool(token_a, token_b, int(fee)).call())
        if factory_reported.lower() != pool_address.lower():
            raise ValueError("Factory getPool(tokenA, tokenB, fee) does not match configured pool")
        actual_factory = _address(pool.functions.factory().call())
        if actual_factory.lower() != factory_address.lower():
            raise ValueError("Pool factory() does not match configured factory")
        token0, token1 = _address(pool.functions.token0().call()), _address(pool.functions.token1().call())
        if {token0.lower(), token1.lower()} != {token_a.lower(), token_b.lower()}:
            raise ValueError("Pool token0/token1 do not match configured token pair")
        actual_fee = int(pool.functions.fee().call())
        if actual_fee != int(fee):
            raise ValueError(f"Pool fee mismatch: configured {fee}, on-chain {actual_fee}")
        spacing = int(pool.functions.tickSpacing().call())
        factory_spacing = int(factory.functions.feeAmountTickSpacing(int(fee)).call())
        if spacing <= 0 or factory_spacing <= 0 or spacing != factory_spacing:
            raise ValueError("Pool tickSpacing does not match the Factory fee tier configuration")
        token0_contract = self.w3.eth.contract(address=token0, abi=ERC20_ABI)
        token1_contract = self.w3.eth.contract(address=token1, abi=ERC20_ABI)
        decimals0 = int(token0_contract.functions.decimals().call())
        decimals1 = int(token1_contract.functions.decimals().call())
        if not 0 <= decimals0 <= 255 or not 0 <= decimals1 <= 255:
            raise ValueError("Invalid on-chain token decimals")
        slot = pool.functions.slot0().call()
        sqrt_price, tick, unlocked = int(slot[0]), int(slot[1]), bool(slot[6])
        liquidity = int(pool.functions.liquidity().call())
        if sqrt_price <= 0 or liquidity <= 0 or not unlocked:
            raise ValueError("Pool has no active price/liquidity or is locked")
        if tick < -887272 or tick > 887272:
            raise ValueError("Pool current tick is outside V3 bounds")
        block = self.w3.eth.get_block("latest")
        return V3PoolSnapshot(
            int(expected_chain_id), factory_address, pool_address, token0, token1,
            decimals0, decimals1, actual_fee, spacing, sqrt_price, tick, liquidity,
            unlocked, int(block["number"]),
            datetime.fromtimestamp(int(block["timestamp"]), timezone.utc).isoformat(),
        )


class V3QuoterV2:
    """A QuoterV2 is accepted only after code and successful quote-call validation."""

    def __init__(self, web3: Web3, address: str):
        self.w3 = web3
        self.address = _address(address)
        if not self.w3.eth.get_code(self.address):
            raise ValueError("QuoterV2 address has no deployed bytecode")
        self.contract = self.w3.eth.contract(address=self.address, abi=QUOTER_V2_ABI)

    def quote(self, snapshot: V3PoolSnapshot, token_in: str, amount_in: Decimal) -> V3Quote:
        token_in = _address(token_in)
        if token_in.lower() == snapshot.token0.lower():
            token_out = snapshot.token1
            decimals_in, decimals_out = snapshot.token0_decimals, snapshot.token1_decimals
            spot = (Decimal(snapshot.sqrt_price_x96) ** 2 / Decimal(2 ** 192)
                    * (Decimal(10) ** (decimals_in - decimals_out)))
        elif token_in.lower() == snapshot.token1.lower():
            token_out = snapshot.token0
            decimals_in, decimals_out = snapshot.token1_decimals, snapshot.token0_decimals
            spot = (Decimal(2 ** 192) / Decimal(snapshot.sqrt_price_x96) ** 2
                    * (Decimal(10) ** (decimals_in - decimals_out)))
        else:
            raise ValueError("token_in is not part of this V3 pool")
        raw_in = int(amount_in * (Decimal(10) ** decimals_in))
        if raw_in <= 0:
            raise ValueError("Quote amount rounds to zero")
        amount_in = from_raw(raw_in, decimals_in)
        params = (token_in, token_out, raw_in, snapshot.fee, 0)
        result = self.contract.functions.quoteExactInputSingle(params).call()
        raw_out, sqrt_after, ticks_crossed, gas_estimate = map(int, result)
        if raw_out <= 0 or sqrt_after <= 0:
            raise ValueError("QuoterV2 returned a non-positive output or price")
        amount_out = from_raw(raw_out, decimals_out)
        effective = amount_out / amount_in
        fee_factor = Decimal(1) - Decimal(snapshot.fee) / Decimal(1_000_000)
        effective_without_fee = effective / fee_factor
        impact = max(Decimal(0), (Decimal(1) - effective_without_fee / spot) * Decimal(10_000))
        block = self.w3.eth.get_block("latest")
        return V3Quote(token_in, token_out, amount_in, amount_out, effective, spot,
                       impact, ticks_crossed, gas_estimate, int(block["number"]),
                       datetime.fromtimestamp(int(block["timestamp"]), timezone.utc).isoformat(),
                       snapshot.fee, snapshot.tick, sqrt_after)

    def validate_deployment(self, snapshot: V3PoolSnapshot, token_in: str,
                             probe_amount: Decimal) -> V3Quote:
        # Successful pool-specific quote validates ABI, address, token, fee and call path.
        return self.quote(snapshot, token_in, probe_amount)


def load_registry(path: str | Path = "market_registry.json") -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("schema_version") != 2:
        raise ValueError("Unsupported registry schema; expected V3-only schema_version 2")
    sizes = [_decimal(value) for value in data.get("capital_sizes_usdt", [])]
    if sizes != list(CAPITAL_SIZES_USDT):
        raise ValueError("Registry must define the six required capital sizes in order")
    weights = {key: _decimal(value) for key, value in data.get("ranking_weights", {}).items()}
    expected = {"net_profit": Decimal(".45"), "roi": Decimal(".35"), "capital_capacity": Decimal(".20")}
    if weights != expected:
        raise ValueError("Ranking weights must be net_profit=.45, roi=.35, capital_capacity=.20")
    ids = [int(chain["id"]) for chain in data.get("chains", [])]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate chain IDs in registry")
    allowed = {1, 56, 137, 42161, 8453, 10, 43114, 32769}
    if set(ids) != allowed:
        raise ValueError("Registry chain IDs differ from the required network set")
    seen: set[tuple[int, str]] = set()
    for market in data.get("markets", []):
        if market.get("version") != "V3" or market.get("quote_adapter") != "v3_quoter_v2":
            raise ValueError("Only V3 markets and the V3 QuoterV2 adapter are permitted")
        if market.get("enabled") is True:
            required = ("factory", "pool", "quoter", "token0", "token1", "fee")
            if any(not market.get(key) for key in required):
                raise ValueError(f"Enabled V3 market {market.get('id')} is missing required contract metadata")
            if market.get("status") != "verified":
                raise ValueError("Only explicitly verified markets may be enabled")
        pool = str(market.get("pool") or "").lower()
        key = (int(market["chain_id"]), pool)
        if pool and key in seen:
            raise ValueError(f"Duplicate V3 pool in registry: {key}")
        if pool:
            seen.add(key)
    return data


def rpc_for_chain(chain: dict[str, Any]) -> str:
    value = os.getenv(str(chain["rpc_env"]), "").strip()
    if not value:
        raise RuntimeError(f"RPC environment variable {chain['rpc_env']} is not configured")
    return value


def _normalise(values: list[Decimal]) -> list[Decimal]:
    maximum = max(values, default=Decimal(0))
    if maximum <= 0:
        return [Decimal(0) for _ in values]
    return [max(value, Decimal(0)) / maximum for value in values]


@dataclass(frozen=True)
class RankedOpportunity:
    route: str
    market_a: str
    market_b: str | None
    capital_usdt: Decimal
    gross_profit_usdt: Decimal
    gas_cost_usdt: Decimal
    net_profit_usdt: Decimal
    roi_percent: Decimal
    capacity_usdt: Decimal
    score: Decimal
    rank: int
    status: str = "VERIFIED"


def rank_opportunities(rows: list[dict[str, Any]], weights: dict[str, Any]) -> list[RankedOpportunity]:
    """Rank only verified, complete, positive-net opportunities; never infer missing cost as zero."""
    groups: dict[Decimal, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("status") != "VERIFIED" or row.get("cross_chain") is True:
            continue
        required = ("gross_profit_usdt", "gas_cost_usdt", "net_profit_usdt",
                    "roi_percent", "capacity_usdt", "capital_usdt")
        if any(row.get(key) is None for key in required):
            continue
        if _decimal(row["net_profit_usdt"]) <= 0 or _decimal(row["gas_cost_usdt"]) < 0:
            continue
        groups.setdefault(_decimal(row["capital_usdt"]), []).append(row)
    output: list[RankedOpportunity] = []
    w_profit, w_roi, w_capacity = (_decimal(weights[k]) for k in ("net_profit", "roi", "capital_capacity"))
    for capital, group in groups.items():
        profits = _normalise([_decimal(row["net_profit_usdt"]) for row in group])
        rois = _normalise([_decimal(row["roi_percent"]) for row in group])
        capacities = _normalise([_decimal(row["capacity_usdt"]) for row in group])
        scored = []
        for row, p, r, c in zip(group, profits, rois, capacities):
            score = (p*w_profit + r*w_roi + c*w_capacity) * 100
            scored.append((score, row))
        scored.sort(key=lambda item: (item[0], _decimal(item[1]["net_profit_usdt"])), reverse=True)
        for rank, (score, row) in enumerate(scored, 1):
            output.append(RankedOpportunity(
                str(row["route"]), str(row["market_a"]),
                str(row["market_b"]) if row.get("market_b") else None,
                capital, _decimal(row["gross_profit_usdt"]), _decimal(row["gas_cost_usdt"]),
                _decimal(row["net_profit_usdt"]), _decimal(row["roi_percent"]),
                _decimal(row["capacity_usdt"]), score.quantize(Decimal(".01")), rank,
            ))
    return sorted(output, key=lambda item: (item.capital_usdt, item.rank))


def simulate_sell_base_into_bids(base_amount: Decimal, bids: Iterable[tuple[Decimal, Decimal]]) -> Decimal:
    """Return quote proceeds for a market sell, consuming bid depth from best to worst."""
    remaining, proceeds = base_amount, Decimal(0)
    if base_amount <= 0:
        raise ValueError("Base amount must be positive")
    for price, size in sorted(bids, key=lambda level: level[0], reverse=True):
        take = min(remaining, size)
        if price <= 0 or size < 0:
            raise ValueError("Invalid bid level")
        proceeds += take * price
        remaining -= take
        if remaining <= 0:
            return proceeds
    raise ValueError("Insufficient CEX bid depth for requested base amount")


def simulate_buy_base_with_quote(quote_budget: Decimal,
                                 asks: Iterable[tuple[Decimal, Decimal]]) -> Decimal:
    """Return base acquired with a quote budget, consuming ask depth from best to worst."""
    remaining, acquired = quote_budget, Decimal(0)
    if quote_budget <= 0:
        raise ValueError("Quote budget must be positive")
    for price, size in sorted(asks, key=lambda level: level[0]):
        if price <= 0 or size < 0:
            raise ValueError("Invalid ask level")
        level_cost = price * size
        spend = min(remaining, level_cost)
        acquired += spend / price
        remaining -= spend
        if remaining <= 0:
            return acquired
    if remaining > Decimal("0.000000000000000001"):
        raise ValueError("Insufficient CEX ask depth for requested quote budget")
    return acquired


def evaluate_dex_cex(*, market_id: str, capital_sizes_usdt: Iterable[Decimal],
                     quote_to_base: Callable[[Decimal], Decimal],
                     base_to_quote: Callable[[Decimal], Decimal],
                     cex_bid: Decimal, cex_ask: Decimal, cex_fee_rate: Decimal,
                     quote_asset_usd_price: Decimal, gas_cost_usdt: Decimal | None,
                     status: str, asset_mapping_verified: bool, capacity_search_max_usdt: Decimal | None = None,
                     usd_prices_verified: bool = False, cex_depth_verified: bool = False,
                     cex_sell_base: Callable[[Decimal], Decimal] | None = None,
                     cex_buy_base_with_quote: Callable[[Decimal], Decimal] | None = None,
                     slippage_bps: int | None = None) -> list[dict[str, Any]]:
    if status != "VERIFIED" or not asset_mapping_verified:
        return [{"route":"DEX_CEX","market_a":market_id,"status":"UNVERIFIED",
                 "reason":"V3 market or CEX asset mapping is not verified"}]
    if min(cex_bid, cex_ask, quote_asset_usd_price) <= 0 or cex_bid > cex_ask:
        raise ValueError("Invalid CEX prices or quote asset conversion")
    if not 0 <= cex_fee_rate < 1:
        raise ValueError("Invalid CEX fee rate")
    if (not usd_prices_verified or not cex_depth_verified
            or cex_sell_base is None or cex_buy_base_with_quote is None
            or slippage_bps is None or not 0 <= slippage_bps < 10_000):
        return [{"route":"DEX_CEX","market_a":market_id,"status":"INSUFFICIENT_DATA",
                 "reason":"Verified USD conversion and executable CEX order-book depth callbacks are required"}]
    capital_sizes = tuple(capital_sizes_usdt)
    rows = []
    for capital in capital_sizes:
        quote_in = capital / quote_asset_usd_price
        # Buy base on V3, sell base on CEX.
        base_out = quote_to_base(quote_in) * (Decimal(1)-Decimal(slippage_bps)/Decimal(10_000))
        gross_a = cex_sell_base(base_out) * (1-cex_fee_rate) * quote_asset_usd_price - capital
        # Buy base on CEX, sell base into V3.
        base_bought = cex_buy_base_with_quote(quote_in / (1+cex_fee_rate))
        quote_out = base_to_quote(base_bought) * (Decimal(1)-Decimal(slippage_bps)/Decimal(10_000))
        gross_b = quote_out * quote_asset_usd_price - capital
        for route, gross in (("DEX_CEX", gross_a), ("CEX_DEX", gross_b)):
            net = gross - gas_cost_usdt if gas_cost_usdt is not None else None
            def gross_at(size: Decimal, selected_route: str = route) -> Decimal:
                qin = size / quote_asset_usd_price
                if selected_route == "DEX_CEX":
                    base_amount = quote_to_base(qin) * (Decimal(1)-Decimal(slippage_bps)/Decimal(10_000))
                    return cex_sell_base(base_amount) * (1-cex_fee_rate) * quote_asset_usd_price - size
                bought = cex_buy_base_with_quote(qin / (1+cex_fee_rate))
                return base_to_quote(bought) * (Decimal(1)-Decimal(slippage_bps)/Decimal(10_000)) * quote_asset_usd_price - size
            capacity_error = None
            try:
                capacity = (calculate_quote_capacity(gross_at, minimum_input=min(capital_sizes),
                             maximum_input=capacity_search_max_usdt, gas_cost=gas_cost_usdt)
                             if gas_cost_usdt is not None and capacity_search_max_usdt is not None else None)
            except Exception as exc:
                capacity, capacity_error = None, f"{type(exc).__name__}: {exc}"
            rows.append({"route":route,"market_a":market_id,"market_b":None,
                         "capital_usdt":capital,"gross_profit_usdt":gross,"slippage_bps":slippage_bps,
                         "gas_cost_usdt":gas_cost_usdt,"net_profit_usdt":net,
                         "roi_percent":net/capital*100 if net is not None else None,
                         "capacity_usdt":capacity,"status":"VERIFIED" if net is not None and capacity is not None else "INSUFFICIENT_DATA",
                         "reason":None if net is not None and capacity is not None else
                                  (capacity_error or "Capacity bounds/quotes unavailable" if net is not None else "Gas cost unavailable")})
    return rows


def evaluate_dex_dex(*, market_a: dict[str, Any], market_b: dict[str, Any],
                     capital_sizes_usdt: Iterable[Decimal],
                     a_quote_to_base: Callable[[Decimal], Decimal],
                     a_base_to_quote: Callable[[Decimal], Decimal],
                     b_quote_to_base: Callable[[Decimal], Decimal],
                     b_base_to_quote: Callable[[Decimal], Decimal],
                     quote_asset_usd_price: Decimal,
                     gas_cost_a_usdt: Decimal | None, gas_cost_b_usdt: Decimal | None, capacity_search_max_usdt: Decimal | None = None,
                     usd_prices_verified: bool = False, slippage_bps: int | None = None) -> list[dict[str, Any]]:
    if (market_a.get("status") != "VERIFIED" or market_b.get("status") != "VERIFIED"
            or market_a.get("enabled") is not True or market_b.get("enabled") is not True):
        return [{"route":"DEX_DEX","market_a":market_a["id"],"market_b":market_b["id"],
                 "status":"UNVERIFIED","reason":"Both V3 markets must be independently verified and enabled"}]
    compatible = (market_a["chain_id"] == market_b["chain_id"]
                  and market_a["token_base"]["address"].lower() == market_b["token_base"]["address"].lower()
                  and market_a["token_quote"]["address"].lower() == market_b["token_quote"]["address"].lower()
                  and int(market_a["token_base"]["decimals"]) == int(market_b["token_base"]["decimals"])
                  and int(market_a["token_quote"]["decimals"]) == int(market_b["token_quote"]["decimals"]))
    if not compatible:
        return [{"route":"DEX_DEX","market_a":market_a["id"],"market_b":market_b["id"],
                 "status":"REJECTED","reason":"Markets are not same-chain and exact-token compatible"}]
    if not usd_prices_verified or slippage_bps is None or not 0 <= slippage_bps < 10_000:
        return [{"route":"DEX_DEX","market_a":market_a["id"],"market_b":market_b["id"],
                 "status":"INSUFFICIENT_DATA","reason":"Verified USD conversion and explicit slippage bound are required"}]
    gas = gas_cost_a_usdt + gas_cost_b_usdt if gas_cost_a_usdt is not None and gas_cost_b_usdt is not None else None
    capital_sizes = tuple(capital_sizes_usdt)
    rows = []
    for capital in capital_sizes:
        quote_in = capital / quote_asset_usd_price
        for source, destination, first, second in (
            (market_a, market_b, a_quote_to_base, b_base_to_quote),
            (market_b, market_a, b_quote_to_base, a_base_to_quote),
        ):
            after_first = first(quote_in) * (Decimal(1)-Decimal(slippage_bps)/Decimal(10_000))
            quote_out = second(after_first) * (Decimal(1)-Decimal(slippage_bps)/Decimal(10_000))
            gross = (quote_out-quote_in)*quote_asset_usd_price
            net = gross-gas if gas is not None else None
            def gross_at(size: Decimal, first_quote: Callable[[Decimal], Decimal] = first,
                         second_quote: Callable[[Decimal], Decimal] = second) -> Decimal:
                q = size / quote_asset_usd_price
                after_first_quote = first_quote(q) * (Decimal(1)-Decimal(slippage_bps)/Decimal(10_000))
                return (second_quote(after_first_quote) * (Decimal(1)-Decimal(slippage_bps)/Decimal(10_000))-q)*quote_asset_usd_price
            capacity_error = None
            try:
                capacity = (calculate_quote_capacity(gross_at,minimum_input=min(capital_sizes),
                             maximum_input=capacity_search_max_usdt,gas_cost=gas)
                             if gas is not None and capacity_search_max_usdt is not None else None)
            except Exception as exc:
                capacity, capacity_error = None, f"{type(exc).__name__}: {exc}"
            rows.append({"route":"DEX_DEX","market_a":source["id"],"market_b":destination["id"],
                         "capital_usdt":capital,"gross_profit_usdt":gross,"slippage_bps":slippage_bps,"gas_cost_usdt":gas,
                         "net_profit_usdt":net,"roi_percent":net/capital*100 if net is not None else None,
                         "capacity_usdt":capacity,"status":"VERIFIED" if net is not None and capacity is not None else "INSUFFICIENT_DATA",
                         "reason":None if net is not None and capacity is not None else
                                  (capacity_error or "Capacity bounds/quotes unavailable" if net is not None else "One or both gas costs unavailable")})
    return rows


def calculate_quote_capacity(quote_profit: Callable[[Decimal], Decimal], *,
                             minimum_input: Decimal, maximum_input: Decimal,
                             gas_cost: Decimal, tolerance: Decimal = Decimal("0.01"),
                             max_iterations: int = 64) -> Decimal:
    """Binary-search quote-derived positive-net capacity; not the largest profitable sample size."""
    if minimum_input <= 0 or maximum_input < minimum_input or gas_cost < 0 or tolerance <= 0:
        raise ValueError("Invalid capacity search bounds or costs")
    # Profitability need not be monotonic: fixed gas can make small sizes
    # unprofitable while larger sizes become profitable. Find the last
    # profitable interval using real quote callbacks, then refine its boundary.
    samples = 256
    def net_at(amount: Decimal) -> Decimal:
        return quote_profit(amount)-gas_cost
    grid = [minimum_input + (maximum_input-minimum_input)*Decimal(i)/Decimal(samples)
            for i in range(samples+1)]
    profitable = [i for i, amount in enumerate(grid) if net_at(amount) > 0]
    if not profitable:
        return Decimal(0)
    index = profitable[-1]
    if index == samples:
        return maximum_input
    low, high = grid[index], grid[index+1]
    for _ in range(max_iterations):
        if high-low <= tolerance:
            break
        mid = (low+high)/2
        if net_at(mid) > 0:
            low = mid
        else:
            high = mid
    return low



def estimate_gas_cost_usdt(gas_units: int, gas_price_wei: int,
                           native_token_usd_price: Decimal) -> Decimal:
    """Convert an explicit gas estimate and current gas price to USD; never defaults to zero."""
    if gas_units <= 0 or gas_price_wei <= 0 or native_token_usd_price <= 0:
        raise ValueError("Gas units, gas price and native token USD price must be positive")
    return (Decimal(gas_units) * Decimal(gas_price_wei) / Decimal(10**18)
            * native_token_usd_price)


def quote_capital_grid(snapshot: V3PoolSnapshot, quoter: V3QuoterV2, *,
                       capital_sizes_usdt: Iterable[Decimal],
                       token0_usd_price: Decimal, token1_usd_price: Decimal) -> list[dict[str, Any]]:
    """Quote USD-capital equivalents in both directions using independently verified USD prices."""
    if token0_usd_price <= 0 or token1_usd_price <= 0:
        raise ValueError("Both token USD prices must be independently verified and positive")
    rows = []
    for capital in capital_sizes_usdt:
        for token_in, usd_price in ((snapshot.token0, token0_usd_price),
                                    (snapshot.token1, token1_usd_price)):
            quote = quoter.quote(snapshot, token_in, capital / usd_price)
            rows.append({"capital_usdt":capital,"direction":f"{quote.token_in}->{quote.token_out}",
                         "amount_in":quote.amount_in,"amount_out":quote.amount_out,
                         "effective_price":quote.effective_price,"spot_price":quote.spot_price,
                         "price_impact_bps":quote.price_impact_bps,
                         "initialized_ticks_crossed":quote.initialized_ticks_crossed,
                         "gas_estimate":quote.gas_estimate,"fee_units":quote.fee_units,
                         "fee_bps":Decimal(quote.fee_units)/Decimal(100),
                         "tick_before":quote.tick_before,"sqrt_price_x96_after":quote.sqrt_price_x96_after,
                         "pool_liquidity":snapshot.liquidity,"pool_snapshot_block":snapshot.block_number,
                         "block_number":quote.block_number,"timestamp_utc":quote.timestamp_utc,
                         "status":"VERIFIED"})
    return rows


async def discover_dexscreener_pairs(query: str, allowed_chain_names: set[str],
                                     minimum_liquidity_usd: Decimal = Decimal("10000"),
                                     minimum_volume_24h_usd: Decimal = Decimal("10000")) -> list[dict[str, Any]]:
    """DexScreener provides candidates only; it cannot certify V3 protocol version."""
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(DEXSCREENER_SEARCH, params={"q":query}) as response:
            response.raise_for_status()
            payload = await response.json()
    results = []
    seen_pools: set[tuple[str,str]] = set()
    for pair in payload.get("pairs") or []:
        liquidity = _decimal((pair.get("liquidity") or {}).get("usd") or 0)
        volume = _decimal((pair.get("volume") or {}).get("h24") or 0)
        chain = str(pair.get("chainId") or "").lower()
        pair_address = str(pair.get("pairAddress") or "").lower()
        duplicate_key = (chain,pair_address)
        if chain not in allowed_chain_names or not pair_address or duplicate_key in seen_pools or liquidity < minimum_liquidity_usd or volume < minimum_volume_24h_usd:
            continue
        seen_pools.add(duplicate_key)
        results.append({"chain":chain,"dex":pair.get("dexId"),"pair_address":pair.get("pairAddress"),
                        "base_token":pair.get("baseToken"),"quote_token":pair.get("quoteToken"),
                        "price_usd":pair.get("priceUsd"),"liquidity_usd":str(liquidity),
                        "volume_24h_usd":str(volume),"url":pair.get("url"),
                        "version":"UNKNOWN","status":"UNVERIFIED",
                        "reason":"DexScreener metadata is discovery-only; verify V3 factory and pool on-chain"})
    return sorted(results,key=lambda row:_decimal(row["liquidity_usd"]),reverse=True)


def serialise(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return {key:serialise(item) for key,item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key):serialise(item) for key,item in value.items()}
    if isinstance(value, (list,tuple)):
        return [serialise(item) for item in value]
    return value


def write_reports(rows: list[dict[str, Any]], output_dir: str | Path, prefix: str = "arbiV3") -> tuple[Path,Path]:
    target = Path(output_dir)
    target.mkdir(parents=True,exist_ok=True)
    json_path, csv_path = target/f"{prefix}.json", target/f"{prefix}.csv"
    json_path.write_text(json.dumps(serialise(rows),indent=2,ensure_ascii=False),encoding="utf-8")
    columns = sorted({key for row in rows for key in row})
    with csv_path.open("w",newline="",encoding="utf-8") as handle:
        writer = csv.DictWriter(handle,fieldnames=columns)
        writer.writeheader()
        for row in rows:
            output_row = {}
            for key,value in row.items():
                encoded = serialise(value)
                output_row[key] = json.dumps(encoded,ensure_ascii=False) if isinstance(encoded,(dict,list,tuple)) else encoded
            writer.writerow(output_row)
    return json_path,csv_path


def verify_network_connectivity(chains: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check configured RPC reachability and chain ID; this does not certify any DEX."""
    reports = []
    for chain in chains:
        try:
            rpc = rpc_for_chain(chain)
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            if not w3.is_connected():
                raise ConnectionError("RPC is not reachable")
            actual = int(w3.eth.chain_id)
            expected = int(chain["id"])
            reports.append({"chain":chain["name"],"expected_chain_id":expected,
                            "actual_chain_id":actual,
                            "status":"RPC_VERIFIED" if actual == expected else "REJECTED",
                            "reason":None if actual == expected else "Chain ID mismatch"})
        except Exception as exc:
            reports.append({"chain":chain["name"],"expected_chain_id":int(chain["id"]),
                            "actual_chain_id":None,"status":"UNVERIFIED",
                            "reason":f"{type(exc).__name__}: {exc}"})
    return reports


def verify_market(market: dict[str, Any], chain: dict[str, Any]) -> tuple[V3PoolSnapshot,V3QuoterV2]:
    for key in ("factory","pool","quoter"):
        if not market.get(key):
            raise ValueError(f"Missing required V3 deployment address: {key}")
    if market.get("version") != "V3" or market.get("quote_adapter") != "v3_quoter_v2":
        raise ValueError("Only V3 QuoterV2 markets can be validated")
    rpc = os.getenv(chain["rpc_env"],"").strip()
    if not rpc:
        raise RuntimeError(f"RPC not configured: {chain['rpc_env']}")
    w3 = Web3(Web3.HTTPProvider(rpc,request_kwargs={"timeout":20}))
    if not w3.is_connected():
        raise ConnectionError("RPC connectivity failed")
    snapshot = V3PoolValidator(w3).validate(
        expected_chain_id=int(chain["id"]),factory_address=market["factory"],
        pool_address=market["pool"],token_a=market["token0"]["address"],
        token_b=market["token1"]["address"],fee=int(market["fee"]),
        slot0_fee_protocol_type=str(market.get("slot0_fee_protocol_type","uint8")))
    if (market["token0"]["address"].lower() != snapshot.token0.lower()
            or market["token1"]["address"].lower() != snapshot.token1.lower()):
        raise ValueError("Configured token0/token1 ordering differs from on-chain pool ordering")
    configured_decimals = {market["token0"]["address"].lower():int(market["token0"]["decimals"]),
                           market["token1"]["address"].lower():int(market["token1"]["decimals"])}
    actual_decimals = {snapshot.token0.lower():snapshot.token0_decimals,
                       snapshot.token1.lower():snapshot.token1_decimals}
    if configured_decimals != actual_decimals:
        raise ValueError("Configured token decimals do not match on-chain ERC-20 decimals")
    for token in (market["token0"],market["token1"]):
        if token.get("symbol"):
            token_contract = w3.eth.contract(address=_address(token["address"]),abi=ERC20_ABI)
            actual_symbol = str(token_contract.functions.symbol().call())
            if actual_symbol != str(token["symbol"]):
                raise ValueError(f"Token symbol mismatch for {token['address']}: expected {token['symbol']}, got {actual_symbol}")
    quoter = V3QuoterV2(w3,market["quoter"])
    probe_token = market.get("probe_token") or market["token0"]["address"]
    probe_amount = _decimal(market.get("probe_amount","0.000001"))
    quoter.validate_deployment(snapshot,probe_token,probe_amount)
    return snapshot,quoter
