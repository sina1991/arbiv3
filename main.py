from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

import aiohttp

from arbitrage import evaluate_opportunities
from config import settings
from kucoin_client import KuCoinClient
from models import Direction
from plunderswap_v3 import PlunderSwapV3, PendingTransactionError, from_raw

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("arbiV3")


class CriticalExecutionError(RuntimeError):
    """Stop the process after one venue has executed and the hedge is uncertain."""


async def notify(message: str) -> None:
    if not settings.telegram_token or not settings.telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage"
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json={"chat_id": settings.telegram_chat_id, "text": message}) as response:
                if response.status >= 400:
                    logger.warning("Telegram notification failed: HTTP %s", response.status)
    except Exception:
        logger.exception("Telegram notification failed")


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
    gas_cost = await asyncio.to_thread(dex.gas_cost_usdt, bid)
    opportunities = evaluate_opportunities(
        settings.trade_sizes_zil, sell_quotes, buy_quotes, bid, ask,
        settings.kucoin_fee_rate, dict(zip(settings.trade_sizes_zil, settings.min_profit_by_size)), gas_cost,
    )
    dex_balances = await asyncio.to_thread(dex.balances)
    dex_allowances = await asyncio.to_thread(dex.allowances)
    logger.info(
        "Mode=%s | KuCoin bid/ask=%s/%s | DEX wallet WZIL=%s zUSDT=%s native ZIL=%s",
        settings.trading_mode, bid, ask, dex_balances["wzil"],
        dex_balances["zusdt"], dex_balances["native_zil"],
    )
    for item in opportunities:
        logger.info(
            "Candidate %s size=%s estimated_profit=%s USDT profitable=%s (%s)",
            item.direction.value, item.zil_size, item.estimated_profit_usdt,
            item.executable, item.reason,
        )

    if settings.trading_mode == "DRY_RUN":
        logger.info("DRY_RUN: no transaction signing/broadcast and no KuCoin order submission.")
        best = next((item for item in opportunities if item.executable), None)
        if best:
            await notify(
                f"arbiV3 DRY_RUN candidate: {best.direction.value}, "
                f"size={best.zil_size} ZIL, estimated profit={best.estimated_profit_usdt} USDT"
            )
        return

    cex_balances = await kucoin.account_balances()

    # Refresh both venues immediately before execution and re-evaluate the opportunity.
    live_bid, live_ask = await kucoin.ticker()
    live_gas_cost = await asyncio.to_thread(dex.gas_cost_usdt, live_bid)
    dex_balances = await asyncio.to_thread(dex.balances)
    dex_allowances = await asyncio.to_thread(dex.allowances)
    fresh_candidates = []

    for item in opportunities:
        if not item.executable:
            continue
        gas_zil_required = live_gas_cost / live_bid
        if dex_balances["native_zil"] < gas_zil_required:
            logger.warning("Skip %s: insufficient native ZIL for estimated gas", item.direction.value)
            continue

        if item.direction is Direction.BUY_WZIL_ON_DEX_SELL_ZIL_ON_CEX:
            try:
                amount_in, amount_out = await asyncio.to_thread(dex.quote_buy_wzil, item.zil_size)
            except Exception as exc:
                logger.warning("Fresh exact-output quote failed: %s", exc)
                continue
            dex_cost = from_raw(amount_in, settings.zusdt_decimals)
            cex_proceeds = item.zil_size * live_bid * (Decimal("1") - settings.kucoin_fee_rate)
            fresh_profit = cex_proceeds - dex_cost - live_gas_cost
            if dex_balances["zusdt"] < dex_cost or dex_allowances["zusdt"] < amount_in:
                logger.warning("Skip %s: insufficient zUSDT balance or router allowance", item.direction.value)
                continue
            if cex_balances.get("ZIL", Decimal("0")) < item.zil_size:
                logger.warning("Skip %s: insufficient available ZIL on KuCoin", item.direction.value)
                continue
        else:
            try:
                amount_in, amount_out = await asyncio.to_thread(dex.quote_sell_wzil, item.zil_size)
            except Exception as exc:
                logger.warning("Fresh exact-input quote failed: %s", exc)
                continue
            dex_proceeds = from_raw(amount_out, settings.zusdt_decimals)
            cex_cost = item.zil_size * live_ask * (Decimal("1") + settings.kucoin_fee_rate)
            fresh_profit = dex_proceeds - cex_cost - live_gas_cost
            required_usdt = cex_cost
            if dex_balances["wzil"] < item.zil_size or dex_allowances["wzil"] < amount_in:
                logger.warning("Skip %s: insufficient WZIL balance or router allowance", item.direction.value)
                continue
            if cex_balances.get("USDT", Decimal("0")) < required_usdt:
                logger.warning("Skip %s: insufficient available USDT on KuCoin", item.direction.value)
                continue

        if fresh_profit < settings.profit_threshold(item.zil_size):
            logger.info("Skip %s: fresh estimated profit %s is below threshold", item.direction.value, fresh_profit)
            continue
        fresh_candidates.append((fresh_profit, item, amount_in, amount_out))

    if not fresh_candidates:
        logger.info("No opportunity passed fresh profitability, balance, allowance and gas checks.")
        return

    fresh_candidates.sort(key=lambda row: row[1].zil_size, reverse=True)
    fresh_profit, best, amount_in, amount_out = fresh_candidates[0]

    if best.direction is Direction.BUY_WZIL_ON_DEX_SELL_ZIL_ON_CEX:
        tx_hash = await asyncio.to_thread(dex.execute_swap, "BUY_WZIL", amount_in, amount_out)
        logger.warning("V3 buy WZIL confirmed on-chain: %s", tx_hash)
        cex_side = "sell"
    else:
        tx_hash = await asyncio.to_thread(dex.execute_swap, "SELL_WZIL", amount_in, amount_out)
        logger.warning("V3 sell WZIL confirmed on-chain: %s", tx_hash)
        cex_side = "buy"

    try:
        result = await kucoin.market_order_and_verify(cex_side, best.zil_size)
    except Exception as exc:
        message = (
            f"arbiV3 CRITICAL: DEX transaction {tx_hash} is confirmed but KuCoin hedge "
            f"failed or is uncertain ({exc}). Trading halted; reconcile balances before restart."
        )
        logger.critical(message)
        await notify(message)
        raise CriticalExecutionError(message) from exc

    logger.info("KuCoin execution result: %s", result)
    if not result["fully_filled"]:
        message = (
            f"arbiV3 CRITICAL: partial KuCoin fill after DEX transaction. "
            f"requested={result['requested_size']} filled={result['filled_size']} "
            f"DEX_tx={tx_hash}. Trading halted; inventory may be unbalanced."
        )
        logger.critical(message)
        await notify(message)
        raise CriticalExecutionError(message)
    else:
        await notify(
            f"arbiV3 LIVE trade completed. route={best.direction.value}, "
            f"size={best.zil_size} ZIL, estimated_profit={fresh_profit} USDT, "
            f"DEX_tx={tx_hash}, KuCoin_order={result['order_id']}, "
            f"KuCoin_avg={result['average_price']}"
        )


async def main():
    settings.validate()
    dex = await asyncio.to_thread(PlunderSwapV3, settings)
    async with KuCoinClient(settings) as kucoin:
        logger.info(
            "arbiV3 started in %s mode; active DEX market: PlunderSwap V3 WZIL/zUSDT.",
            settings.trading_mode,
        )
        while True:
            try:
                await run_cycle(dex, kucoin)
            except asyncio.CancelledError:
                raise
            except (PendingTransactionError, CriticalExecutionError) as exc:
                logger.critical("Trading halted for manual reconciliation: %s", exc)
                await notify(f"arbiV3 HALTED: {exc}")
                break
            except Exception:
                logger.exception("Cycle failed; no new trade will be attempted in this cycle")
            await asyncio.sleep(settings.check_interval)


if __name__ == "__main__":
    asyncio.run(main())
