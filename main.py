from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from arbitrage import evaluate_opportunities
from config import settings
from kucoin_client import KuCoinClient
from models import Direction
from plunderswap_v3 import PlunderSwapV3, from_raw, to_raw

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("arbiV3")


def load_quote_maps(dex: PlunderSwapV3):
    sell_quotes: dict[Decimal, Decimal] = {}
    buy_quotes: dict[Decimal, Decimal] = {}
    for size in settings.trade_sizes_zil:
        try:
            _, amount_out = dex.quote_sell_wzil(size)
            sell_quotes[size] = from_raw(amount_out, settings.zusdt_decimals)
        except Exception as exc:
            logger.warning("V3 exact-input quote unavailable for %s WZIL: %s", size, exc)
        try:
            amount_in, _ = dex.quote_buy_wzil(size)
            buy_quotes[size] = from_raw(amount_in, settings.zusdt_decimals)
        except Exception as exc:
            logger.warning("V3 exact-output quote unavailable for %s WZIL: %s", size, exc)
    return sell_quotes, buy_quotes


async def run_cycle(dex: PlunderSwapV3, kucoin: KuCoinClient):
    bid, ask = await kucoin.ticker()
    sell_quotes, buy_quotes = await asyncio.to_thread(load_quote_maps, dex)
    gas_cost = await asyncio.to_thread(dex.gas_cost_usdt)
    opportunities = evaluate_opportunities(
        settings.trade_sizes_zil, sell_quotes, buy_quotes, bid, ask,
        settings.kucoin_fee_rate, settings.min_profit_usdt, gas_cost,
    )
    balances = await asyncio.to_thread(dex.balances)
    logger.info(
        "Mode=%s | KuCoin bid/ask=%s/%s | V3 wallet WZIL=%s zUSDT=%s native ZIL=%s",
        settings.trading_mode, bid, ask, balances["wzil"], balances["zusdt"], balances["native_zil"]
    )
    for item in opportunities:
        logger.info(
            "Candidate %s size=%s estimated_profit=%s USDT executable=%s (%s)",
            item.direction.value, item.zil_size, item.estimated_profit_usdt,
            item.executable, item.reason,
        )

    if settings.trading_mode == "DRY_RUN":
        logger.info("DRY_RUN: no transaction signing/broadcast and no KuCoin order submission.")
        return

    candidates = [item for item in opportunities if item.executable]
    if not candidates:
        return
    best = candidates[0]
    if best.direction is Direction.BUY_WZIL_ON_DEX_SELL_ZIL_ON_CEX:
        amount_in, amount_out = await asyncio.to_thread(dex.quote_buy_wzil, best.zil_size)
        tx_hash = await asyncio.to_thread(dex.execute_swap, "BUY_WZIL", amount_in, amount_out)
        logger.warning("V3 buy WZIL confirmed on-chain: %s", tx_hash)
        result = await kucoin.market_order_and_verify("sell", best.zil_size)
    else:
        amount_in, amount_out = await asyncio.to_thread(dex.quote_sell_wzil, best.zil_size)
        tx_hash = await asyncio.to_thread(dex.execute_swap, "SELL_WZIL", amount_in, amount_out)
        logger.warning("V3 sell WZIL confirmed on-chain: %s", tx_hash)
        result = await kucoin.market_order_and_verify("buy", best.zil_size)
    logger.info("KuCoin execution result: %s", result)
    if not result["fully_filled"]:
        logger.error(
            "PARTIAL FILL: requested=%s filled=%s; inventory may be unbalanced",
            result["requested_size"], result["filled_size"]
        )


async def main():
    settings.validate()
    if not settings.wallet_address:
        raise RuntimeError("WALLET_ADDRESS is required for V3 router quotes in both modes")
    dex = await asyncio.to_thread(PlunderSwapV3, settings)
    async with KuCoinClient(settings) as kucoin:
        logger.info("arbiV3 started in %s mode; DEX market is PlunderSwap V3 WZIL/zUSDT only.", settings.trading_mode)
        while True:
            try:
                await run_cycle(dex, kucoin)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Cycle failed; no new trade will be attempted this cycle")
            await asyncio.sleep(settings.check_interval)


if __name__ == "__main__":
    asyncio.run(main())
