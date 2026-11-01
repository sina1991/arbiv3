from __future__ import annotations

import asyncio
from decimal import Decimal
from itertools import combinations
from typing import Any

from kucoin_client import KuCoinClient
from market_scanner import (
    CAPITAL_SIZES_USDT, estimate_gas_cost_usdt, evaluate_dex_cex,
    evaluate_dex_dex, quote_capital_grid, rank_opportunities,
    simulate_buy_base_with_quote, simulate_sell_base_into_bids,
    verify_market, write_reports, serialise,
)


def _gas_cost(market: dict[str, Any], quote_rows: list[dict[str, Any]], quoter: Any) -> Decimal | None:
    if not market.get("native_token_usd_price_verified"):
        return None
    native_usd = Decimal(str(market.get("native_token_usd_price", "0")))
    if native_usd <= 0 or not quote_rows:
        return None
    try:
        gas_units = max(int(row["gas_estimate"]) for row in quote_rows)
        multiplier = Decimal(str(market.get("gas_safety_multiplier","1.2")))
        if multiplier < 1:
            return None
        gas_units = int((Decimal(gas_units)*multiplier).to_integral_value(rounding="ROUND_CEILING"))
        gas_price_wei = int(quoter.w3.eth.gas_price)
        return estimate_gas_cost_usdt(gas_units, gas_price_wei, native_usd)
    except Exception:
        return None


async def scan_registry(registry: dict[str, Any], chains: dict[int, dict[str, Any]],
                        output_dir: str = "reports") -> dict[str, Any]:
    """Validate enabled V3 markets, quote the six-size grid, evaluate same-chain routes and report."""
    enabled = [m for m in registry["markets"]
               if m.get("enabled") is True and m.get("status") == "verified"]
    validation_rows: list[dict[str, Any]] = []
    quote_rows: list[dict[str, Any]] = []
    market_context: list[dict[str, Any]] = []

    for market in enabled:
        try:
            snapshot, quoter = verify_market(market, chains[int(market["chain_id"])])
            prices_ready = (market.get("usd_prices_verified") is True
                            and market.get("token0_usd_price") is not None
                            and market.get("token1_usd_price") is not None)
            if not prices_ready:
                validation_rows.append({"market_id":market["id"],"status":"INSUFFICIENT_DATA",
                    "reason":"Both token USD prices and their verified-source flag are required"})
                market_context.append({"market":market,"snapshot":snapshot,"quoter":quoter,"quotes":[]})
                continue
            quotes = quote_capital_grid(snapshot, quoter,
                capital_sizes_usdt=CAPITAL_SIZES_USDT,
                token0_usd_price=Decimal(str(market["token0_usd_price"])),
                token1_usd_price=Decimal(str(market["token1_usd_price"])))
            for row in quotes:
                quote_rows.append({"market_id":market["id"],**row})
            validation_rows.append({"market_id":market["id"],"status":"VERIFIED",
                                    "pool":snapshot,"quoter":quoter.address})
            market_context.append({"market":market,"snapshot":snapshot,"quoter":quoter,"quotes":quotes})
        except Exception as exc:
            status = "REJECTED" if isinstance(exc,ValueError) else "UNVERIFIED"
            validation_rows.append({"market_id":market["id"],"status":status,
                                    "reason":f"{type(exc).__name__}: {exc}"})

    opportunity_rows: list[dict[str, Any]] = []
    async with KuCoinClient() as kucoin:
        for ctx in market_context:
            market, snapshot, quoter, quotes = (ctx["market"],ctx["snapshot"],ctx["quoter"],ctx["quotes"])
            cex = market.get("cex") or {}
            if not cex.get("symbol"):
                continue
            base_address = str(cex.get("base_token_address") or "")
            quote_address = str(cex.get("quote_token_address") or "")
            if not base_address or not quote_address:
                opportunity_rows.append({"route":"DEX_CEX","market_a":market["id"],
                    "status":"INSUFFICIENT_DATA","reason":"CEX mapping must include exact base and quote token contract addresses"})
                continue
            if cex.get("fee_rate") is None or cex.get("fee_rate_verified") is not True:
                opportunity_rows.append({"route":"DEX_CEX","market_a":market["id"],
                    "status":"INSUFFICIENT_DATA","reason":"Verified CEX trading fee rate is required"})
                continue
            try:
                bid, ask = await kucoin.ticker(cex["symbol"])
                bids, asks = await kucoin.order_book(cex["symbol"],100)
                depth_verified = bool(bids and asks and bids[0][0] == bid and asks[0][0] == ask)
                token_prices = {
                    market["token0"]["address"].lower():Decimal(str(market["token0_usd_price"])),
                    market["token1"]["address"].lower():Decimal(str(market["token1_usd_price"])),
                }
                quote_usd = token_prices[quote_address.lower()]
                quote_to_base = lambda amount, q=quote_address: quoter.quote(snapshot,q,amount).amount_out
                base_to_quote = lambda amount, b=base_address: quoter.quote(snapshot,b,amount).amount_out
                gas_cost = _gas_cost(market,quotes,quoter)
                rows = evaluate_dex_cex(
                    market_id=market["id"],capital_sizes_usdt=CAPITAL_SIZES_USDT,
                    quote_to_base=quote_to_base,base_to_quote=base_to_quote,
                    cex_bid=bid,cex_ask=ask,
                    cex_fee_rate=Decimal(str(cex["fee_rate"])),
                    quote_asset_usd_price=quote_usd,gas_cost_usdt=gas_cost,
                    status="VERIFIED",asset_mapping_verified=bool(cex.get("asset_mapping_verified")),
                    usd_prices_verified=bool(market.get("usd_prices_verified")),
                    cex_depth_verified=depth_verified,
                    cex_sell_base=lambda amount,book=bids:simulate_sell_base_into_bids(amount,book),
                    cex_buy_base_with_quote=lambda amount,book=asks:simulate_buy_base_with_quote(amount,book),
                    slippage_bps=int(market["slippage_bps"]) if market.get("slippage_bps") is not None else None,
                    capacity_search_max_usdt=Decimal(str(market["capacity_search_max_usdt"]))
                        if market.get("capacity_search_max_usdt") is not None else None,
                )
                for row in rows:
                    direction = (f"{quote_address}->{base_address}" if row["route"]=="DEX_CEX"
                                 else f"{base_address}->{quote_address}")
                    matching = [q for q in quotes if q["capital_usdt"]==row["capital_usdt"]
                                and q["direction"].lower()==direction.lower()]
                    if matching:
                        row["price_impact_bps"] = matching[0]["price_impact_bps"]
                        row["fee_units"] = matching[0]["fee_units"]
                        row["pool_liquidity"] = matching[0]["pool_liquidity"]
                        row["tick_before"] = matching[0]["tick_before"]
                        row["block_number"] = matching[0]["block_number"]
                        row["timestamp_utc"] = matching[0]["timestamp_utc"]
                opportunity_rows.extend(rows)
            except Exception as exc:
                opportunity_rows.append({"route":"DEX_CEX","market_a":market["id"],
                    "status":"INSUFFICIENT_DATA","reason":f"{type(exc).__name__}: {exc}"})

    for left, right in combinations(market_context,2):
        ma, mb = left["market"], right["market"]
        sa, sb = left["snapshot"], right["snapshot"]
        qa, qb = left["quoter"], right["quoter"]
        if not left["quotes"] or not right["quotes"]:
            continue
        if not all(ma.get(k) for k in ("token_base","token_quote")) or not all(mb.get(k) for k in ("token_base","token_quote")):
            continue
        try:
            base_a, quote_a = ma["token_base"]["address"], ma["token_quote"]["address"]
            base_b, quote_b = mb["token_base"]["address"], mb["token_quote"]["address"]
            quote_to_base_a = lambda amount:qa.quote(sa,quote_a,amount).amount_out
            base_to_quote_a = lambda amount:qa.quote(sa,base_a,amount).amount_out
            quote_to_base_b = lambda amount:qb.quote(sb,quote_b,amount).amount_out
            base_to_quote_b = lambda amount:qb.quote(sb,base_b,amount).amount_out
            gas_a, gas_b = _gas_cost(ma,left["quotes"],qa), _gas_cost(mb,right["quotes"],qb)
            quote_usd_a = Decimal(str(ma["quote_token_usd_price"])) if ma.get("quote_token_usd_price") is not None else Decimal(0)
            quote_usd_b = Decimal(str(mb["quote_token_usd_price"])) if mb.get("quote_token_usd_price") is not None else Decimal(0)
            price_map_a = {ma["token0"]["address"].lower():Decimal(str(ma["token0_usd_price"])),
                           ma["token1"]["address"].lower():Decimal(str(ma["token1_usd_price"]))}
            price_map_b = {mb["token0"]["address"].lower():Decimal(str(mb["token0_usd_price"])),
                           mb["token1"]["address"].lower():Decimal(str(mb["token1_usd_price"]))}
            usd_ok = (ma.get("usd_prices_verified") is True and mb.get("usd_prices_verified") is True
                      and quote_usd_a > 0 and quote_usd_a == quote_usd_b
                      and price_map_a.get(quote_a.lower()) == quote_usd_a
                      and price_map_b.get(quote_b.lower()) == quote_usd_b)
            bounds = [Decimal(str(m[k])) for m in (ma,mb) for k in ("capacity_search_max_usdt",)
                      if m.get(k) is not None]
            capacity_bound = min(bounds) if len(bounds)==2 else None
            slip = max(int(ma["slippage_bps"]),int(mb["slippage_bps"])) if ma.get("slippage_bps") is not None and mb.get("slippage_bps") is not None else None
            route_rows = evaluate_dex_dex(
                market_a={**ma,"status":"VERIFIED","enabled":True},
                market_b={**mb,"status":"VERIFIED","enabled":True},
                capital_sizes_usdt=CAPITAL_SIZES_USDT,
                a_quote_to_base=quote_to_base_a,a_base_to_quote=base_to_quote_a,
                b_quote_to_base=quote_to_base_b,b_base_to_quote=base_to_quote_b,
                quote_asset_usd_price=quote_usd_a,
                gas_cost_a_usdt=gas_a,gas_cost_b_usdt=gas_b,
                usd_prices_verified=usd_ok,slippage_bps=slip,
                capacity_search_max_usdt=capacity_bound)
            for row in route_rows:
                if row.get("capital_usdt") is None:
                    continue
                first_market = ma if row["market_a"]==ma["id"] else mb
                second_market = mb if row["market_b"]==mb["id"] else ma
                first_quote_address = first_market["token_quote"]["address"]
                first_base_address = first_market["token_base"]["address"]
                second_base_address = second_market["token_base"]["address"]
                second_quote_address = second_market["token_quote"]["address"]
                first_q = next((q for q in left["quotes"] if q["capital_usdt"]==row["capital_usdt"]
                                and q["direction"].lower()==f"{first_quote_address}->{first_base_address}".lower()),None) if first_market is ma else next((q for q in right["quotes"] if q["capital_usdt"]==row["capital_usdt"] and q["direction"].lower()==f"{first_quote_address}->{first_base_address}".lower()),None)
                second_ctx = right if second_market is mb else left
                second_q = next((q for q in second_ctx["quotes"] if q["capital_usdt"]==row["capital_usdt"]
                                 and q["direction"].lower()==f"{second_base_address}->{second_quote_address}".lower()),None)
                impacts = [q["price_impact_bps"] for q in (first_q,second_q) if q is not None]
                if impacts:
                    row["price_impact_bps"] = sum(impacts,Decimal(0))
                    row["pool_liquidity_a"] = first_q.get("pool_liquidity") if first_q else None
                    row["pool_liquidity_b"] = second_q.get("pool_liquidity") if second_q else None
                    row["fee_units_a"] = first_market["fee"]
                    row["fee_units_b"] = second_market["fee"]
                    row["tick_before_a"] = first_q.get("tick_before") if first_q else None
                    row["tick_before_b"] = second_q.get("tick_before") if second_q else None
                    row["block_number_a"] = first_q.get("block_number") if first_q else None
                    row["block_number_b"] = second_q.get("block_number") if second_q else None
            opportunity_rows.extend(route_rows)
        except Exception as exc:
            opportunity_rows.append({"route":"DEX_DEX","market_a":ma["id"],"market_b":mb["id"],
                                     "status":"INSUFFICIENT_DATA","reason":f"{type(exc).__name__}: {exc}"})

    ranked = rank_opportunities(opportunity_rows,registry["ranking_weights"])
    report = {
        "architecture":"V3_ONLY_CONCENTRATED_LIQUIDITY",
        "status":"COMPLETE_SCAN" if ranked else "INSUFFICIENT_DATA",
        "capital_sizes_usdt":[str(x) for x in CAPITAL_SIZES_USDT],
        "verified_market_count":sum(1 for row in validation_rows if row["status"]=="VERIFIED"),
        "validation":serialise(validation_rows),
        "opportunities":serialise(opportunity_rows),
        "ranked_opportunities":serialise(ranked),
        "limitations":["Only enabled registry markets are scanned",
                       "Capacity is bounded by each market's configured capacity_search_max_usdt",
                       "CEX Level-2 snapshot is a point-in-time depth estimate, not a fill guarantee",
                       "No cross-chain bridge routes are evaluated"],
    }
    write_reports(validation_rows,output_dir,"market_validation")
    write_reports(quote_rows,output_dir,"v3_quotes")
    write_reports(opportunity_rows,output_dir,"opportunities")
    write_reports([report],output_dir,"scan_report")
    return report
