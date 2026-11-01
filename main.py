from __future__ import annotations

import argparse
import asyncio
import json
import logging
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv()

from market_scanner import (
    CAPITAL_SIZES_USDT, discover_dexscreener_pairs, load_registry,
    quote_capital_grid, serialise, verify_market, verify_network_connectivity, write_reports,
)

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger("arbiV3")


async def run() -> None:
    parser=argparse.ArgumentParser(description="arbiV3 — V3-only multi-chain DRY_RUN scanner")
    parser.add_argument("--registry",default="market_registry.json")
    parser.add_argument("--discover",metavar="QUERY")
    parser.add_argument("--min-liquidity",default=None)
    parser.add_argument("--min-volume",default=None)
    parser.add_argument("--validate",metavar="MARKET_ID",help="Validate configured V3 factory, pool and QuoterV2 on-chain")
    parser.add_argument("--token0-usd-price",default=None,help="Independently sourced USD price for market token0")
    parser.add_argument("--token1-usd-price",default=None,help="Independently sourced USD price for market token1")
    parser.add_argument("--check-networks",action="store_true",help="Check configured RPC connectivity and chain IDs")
    parser.add_argument("--output-dir",default="reports")
    args=parser.parse_args()
    registry=load_registry(args.registry)
    chains={int(row["id"]):row for row in registry["chains"]}

    if args.discover:
        d=registry["discovery"]
        candidates=await discover_dexscreener_pairs(
            args.discover,{str(c["name"]).lower() for c in registry["chains"]},
            Decimal(args.min_liquidity or str(d["minimum_liquidity_usd"])),
            Decimal(args.min_volume or str(d["minimum_volume_24h_usd"])))
        json_path,csv_path=write_reports(candidates,args.output_dir,"discovery_candidates")
        print(json.dumps(candidates,indent=2,ensure_ascii=False))
        log.info("Discovery-only candidates: %d | %s | %s",len(candidates),json_path,csv_path)
        return

    if args.check_networks:
        rows=verify_network_connectivity(registry["chains"])
        json_path,csv_path=write_reports(rows,args.output_dir,"network_validation")
        print(json.dumps(rows,indent=2,ensure_ascii=False))
        log.info("RPC checks do not imply V3 DEX support. Reports: %s | %s",json_path,csv_path)
        return

    if args.validate:
        market=next((m for m in registry["markets"] if m["id"]==args.validate),None)
        if market is None:
            raise SystemExit(f"Unknown market id: {args.validate}")
        try:
            snapshot,quoter=verify_market(market,chains[int(market["chain_id"])])
            # Quoter must succeed in both token directions. This is a deployment/ABI
            # smoke test only; profitability requires independently verified USD mapping.
            probe=Decimal(market.get("probe_amount","0.01"))
            q0=quoter.quote(snapshot,snapshot.token0,probe)
            q1=quoter.quote(snapshot,snapshot.token1,probe)
            report={"market_id":market["id"],"status":"VERIFIED","pool":snapshot,
                    "quoter":quoter.address,"probe_quotes":[q0,q1],
                    "note":"Deployment and bidirectional probe quotes verified; market still requires token USD mapping, gas and fee validation before ranking."}
            if args.token0_usd_price is not None and args.token1_usd_price is not None:
                grid=quote_capital_grid(snapshot,quoter,capital_sizes_usdt=CAPITAL_SIZES_USDT,
                    token0_usd_price=Decimal(args.token0_usd_price),
                    token1_usd_price=Decimal(args.token1_usd_price))
                report["capital_quote_grid"]=grid
                report["capital_quote_grid_status"]="QUOTED_NOT_RANKABLE_UNLESS_PRICE_SOURCES_AND_COSTS_ARE_VERIFIED"
            elif args.token0_usd_price is not None or args.token1_usd_price is not None:
                report["capital_quote_grid_status"]="INSUFFICIENT_DATA_BOTH_TOKEN_USD_PRICES_REQUIRED"
        except Exception as exc:
            report={"market_id":market["id"],"status":"UNVERIFIED","reason":f"{type(exc).__name__}: {exc}"}
        rows=[serialise(report)]
        json_path,csv_path=write_reports(rows,args.output_dir,f"validation_{market['id']}")
        print(json.dumps(rows,indent=2,ensure_ascii=False))
        log.info("Validation report: %s | %s",json_path,csv_path)
        return

    enabled=[m for m in registry["markets"] if m.get("enabled") is True and m.get("status")=="verified"]
    report={"architecture":"V3_ONLY_CONCENTRATED_LIQUIDITY","capital_sizes_usdt":[str(x) for x in CAPITAL_SIZES_USDT],
            "ranking_weights":registry["ranking_weights"],
            "networks":[{"chain_id":c["id"],"name":c["name"],"status":c.get("status","UNVERIFIED")} for c in registry["chains"]],
            "markets":[{"id":m["id"],"chain":m["chain"],"dex":m["dex"],"version":m["version"],
                        "status":m.get("status","UNVERIFIED"),"enabled":m.get("enabled",False),
                        "reason":m.get("disabled_reason")} for m in registry["markets"]],
            "ranked_opportunities":[],"status":"INSUFFICIENT_DATA" if not enabled else "READY_FOR_VERIFICATION"}
    json_path,csv_path=write_reports([report],args.output_dir,"scan_report")
    print(json.dumps(report,indent=2,ensure_ascii=False))
    log.info("No V3 market is currently enabled. No arbitrage opportunities were ranked.")
    log.info("Reports written: %s | %s",json_path,csv_path)


if __name__=="__main__":
    asyncio.run(run())
