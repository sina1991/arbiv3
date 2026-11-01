from __future__ import annotations

import argparse
import asyncio
import json
import logging
from decimal import Decimal

from market_scanner import discover_dexscreener_pairs, load_registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("arbiV3")


async def main() -> None:
    parser = argparse.ArgumentParser(description="arbiV3 multi-chain DRY_RUN market research scanner")
    parser.add_argument("--registry", default="market_registry.json", help="Market registry JSON path")
    parser.add_argument("--discover", metavar="QUERY", help="Discover candidate pools through DexScreener")
    parser.add_argument("--min-liquidity", default=None, help="Minimum pool liquidity in USD")
    parser.add_argument("--min-volume", default=None, help="Minimum 24h volume in USD")
    args = parser.parse_args()

    registry = load_registry(args.registry)
    chains = {str(item["name"]).lower() for item in registry["chains"]}

    if args.discover:
        discovery = registry["discovery"]
        candidates = await discover_dexscreener_pairs(
            query=args.discover,
            allowed_chain_names=chains,
            minimum_liquidity_usd=Decimal(args.min_liquidity or discovery["minimum_liquidity_usd"]),
            minimum_volume_24h_usd=Decimal(args.min_volume or discovery["minimum_volume_24h_usd"]),
        )
        print(json.dumps(candidates, indent=2, ensure_ascii=False))
        log.info("Discovery returned %d candidates. These are unverified and are not ranked.", len(candidates))
        return

    enabled = [market for market in registry["markets"] if market.get("enabled") is True]
    disabled = [market for market in registry["markets"] if market.get("enabled") is not True]
    log.info("arbiV3 multi-market scanner | read-only DRY_RUN")
    log.info("Capital sizes (USDT): %s", ", ".join(registry["capital_sizes_usdt"]))
    log.info("Comparison routes: %s", ", ".join(registry["comparison_routes"]))
    log.info("Registered chains: %d | enabled markets: %d | disabled/reference markets: %d",
             len(registry["chains"]), len(enabled), len(disabled))
    for market in enabled:
        log.info("Enabled market: %s | %s | %s | %s",
                 market["id"], market["chain"], market["dex"], market["version"])
    for market in disabled:
        log.info("Not ranked: %s | %s", market["id"], market.get("disabled_reason", market.get("status", "disabled")))
    if not enabled:
        log.warning("No markets are enabled. Discover candidates, validate contracts and quote adapters, then add verified entries to market_registry.json.")
    log.info("No transaction signing, broadcasting, approvals or exchange orders exist in this scanner.")


if __name__ == "__main__":
    asyncio.run(main())
