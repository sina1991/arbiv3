from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import aiohttp

from config import settings
from market_discovery import GeckoTerminalDiscovery

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("arbiV3")


def _serialize(value):
    if isinstance(value, Decimal):
        return str(value)
    return value


async def run_discovery_cycle() -> None:
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        discovery = GeckoTerminalDiscovery(settings.geckoterminal_base_url, session)
        pools, errors = await discovery.discover_many(
            settings.networks, settings.discovery_limit_per_network
        )

    eligible = [
        pool for pool in pools
        if pool.quote_token.symbol in settings.quote_symbols
        and pool.liquidity_usd >= settings.min_pool_liquidity_usd
    ]
    eligible.sort(key=lambda pool: (pool.liquidity_usd, pool.volume_24h_usd), reverse=True)

    report = {
        "mode": "DRY_RUN_ONLY",
        "trade_sizes_usdt": [str(size) for size in settings.trade_sizes_usdt],
        "networks": list(settings.networks),
        "discovered_pool_count": len(pools),
        "eligible_pool_count": len(eligible),
        "scoring_status": "PENDING_VERIFIED_ONCHAIN_QUOTERS",
        "note": (
            "Discovery metadata is not an executable quote. A pool is not scored until "
            "its chain RPC, token decimals, fee model and read-only quote adapter are verified."
        ),
        "errors": errors,
        "pools": [
            {
                "chain": pool.chain,
                "pool_address": pool.pool_address,
                "dex_id": pool.dex_id,
                "dex_name": pool.dex_name,
                "base": asdict(pool.base_token),
                "quote": asdict(pool.quote_token),
                "liquidity_usd": str(pool.liquidity_usd),
                "volume_24h_usd": str(pool.volume_24h_usd),
                "base_token_price_usd": str(pool.price_usd) if pool.price_usd is not None else None,
            }
            for pool in eligible
        ],
    }
    path = Path(settings.report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=_serialize), encoding="utf-8")
    logger.info(
        "DRY_RUN scanner discovery complete: networks=%d pools=%d eligible=%d report=%s",
        len(settings.networks), len(pools), len(eligible), path,
    )
    for network, error in errors.items():
        logger.warning("Discovery failed for %s: %s", network, error)
    logger.info(
        "No profit ranking was fabricated: exact-size on-chain quotes and verified CEX mappings "
        "are required before a market can enter the ranking."
    )


async def main() -> None:
    settings.validate()
    logger.info(
        "Starting Multi-Market DRY_RUN-only scanner; budgets=%s USDT; comparisons=DEX-CEX, DEX-DEX",
        ",".join(map(str, settings.trade_sizes_usdt)),
    )
    while True:
        try:
            await run_discovery_cycle()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Discovery cycle failed")
        await asyncio.sleep(settings.scan_interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
