from __future__ import annotations

from decimal import Decimal
from typing import Callable, Iterable

from models import Opportunity, OrderBook, PoolCandidate

QuoteFn = Callable[[str, str, Decimal], Decimal]


def _sell_base(book: OrderBook, amount_base: Decimal, fee_rate: Decimal) -> Decimal | None:
    remaining = amount_base
    proceeds = Decimal("0")
    for price, available in book.bids:
        filled = min(remaining, available)
        proceeds += filled * price
        remaining -= filled
        if remaining <= 0:
            return proceeds * (Decimal("1") - fee_rate)
    return None


def _buy_base(book: OrderBook, budget_quote: Decimal, fee_rate: Decimal) -> Decimal | None:
    remaining = budget_quote
    acquired = Decimal("0")
    for price, available in book.asks:
        level_cost = price * available
        spend = min(remaining, level_cost)
        acquired += spend / price
        remaining -= spend
        if remaining <= 0:
            return acquired * (Decimal("1") - fee_rate)
    return None


def evaluate_dex_cex(
    pool: PoolCandidate,
    quote_dex: QuoteFn,
    orderbook: OrderBook,
    budgets_usdt: Iterable[Decimal],
    cex_fee_rate: Decimal,
    gas_cost_usdt: Decimal,
    minimum_profit_usdt: Decimal = Decimal("0"),
) -> list[Opportunity]:
    """Evaluate both pre-funded DEX-CEX directions for each quote-currency budget."""
    if pool.quote_token.symbol not in {"USDT", "USDC", "DAI"}:
        return []
    results: list[Opportunity] = []
    base, quote = pool.base_token, pool.quote_token
    for budget in budgets_usdt:
        # DEX buy -> CEX sell.
        try:
            base_bought = quote_dex(quote.address, base.address, budget)
            cex_proceeds = _sell_base(orderbook, base_bought, cex_fee_rate)
        except Exception:
            base_bought, cex_proceeds = Decimal("0"), None
        if cex_proceeds is not None:
            net = cex_proceeds - budget - gas_cost_usdt
            results.append(Opportunity(
                "DEX_CEX", f"{pool.chain}:{pool.pool_address}:DEX_BUY_CEX_SELL",
                pool.chain, base.symbol, quote.symbol, budget, base_bought,
                cex_proceeds, net, (net / budget) * 100, gas_cost_usdt,
                pool.liquidity_usd, pool.volume_24h_usd, Decimal("0"),
                net >= minimum_profit_usdt, "Pre-funded inventory; order-book depth simulated",
            ))

        # CEX buy -> DEX sell.
        try:
            base_bought = _buy_base(orderbook, budget, cex_fee_rate)
            dex_proceeds = quote_dex(base.address, quote.address, base_bought) if base_bought else None
        except Exception:
            base_bought, dex_proceeds = None, None
        if dex_proceeds is not None:
            net = dex_proceeds - budget - gas_cost_usdt
            results.append(Opportunity(
                "CEX_DEX", f"{pool.chain}:{pool.pool_address}:CEX_BUY_DEX_SELL",
                pool.chain, base.symbol, quote.symbol, budget, base_bought,
                dex_proceeds, net, (net / budget) * 100, gas_cost_usdt,
                pool.liquidity_usd, pool.volume_24h_usd, Decimal("0"),
                net >= minimum_profit_usdt, "Pre-funded inventory; order-book depth simulated",
            ))
    return results


def evaluate_dex_dex(
    pool_a: PoolCandidate,
    pool_b: PoolCandidate,
    quote_a: QuoteFn,
    quote_b: QuoteFn,
    budgets_usdt: Iterable[Decimal],
    gas_cost_usdt: Decimal,
    minimum_profit_usdt: Decimal = Decimal("0"),
) -> list[Opportunity]:
    """Compare two pools for the same token pair on the same chain."""
    if pool_a.chain != pool_b.chain:
        return []
    if pool_a.base_token.address.lower() != pool_b.base_token.address.lower():
        return []
    if pool_a.quote_token.address.lower() != pool_b.quote_token.address.lower():
        return []

    results: list[Opportunity] = []
    base, quote = pool_a.base_token, pool_a.quote_token
    for budget in budgets_usdt:
        for buy_pool, sell_pool, buy_quote, sell_quote, label in (
            (pool_a, pool_b, quote_a, quote_b, "A_TO_B"),
            (pool_b, pool_a, quote_b, quote_a, "B_TO_A"),
        ):
            try:
                base_bought = buy_quote(quote.address, base.address, budget)
                quote_proceeds = sell_quote(base.address, quote.address, base_bought)
            except Exception:
                continue
            net = quote_proceeds - budget - gas_cost_usdt
            results.append(Opportunity(
                "DEX_DEX", f"{pool_a.chain}:{buy_pool.pool_address}:{sell_pool.pool_address}:{label}",
                pool_a.chain, base.symbol, quote.symbol, budget, base_bought,
                quote_proceeds, net, (net / budget) * 100, gas_cost_usdt,
                min(pool_a.liquidity_usd, pool_b.liquidity_usd),
                min(pool_a.volume_24h_usd, pool_b.volume_24h_usd),
                Decimal("0"), net >= minimum_profit_usdt,
                "Same-chain, pre-funded inventory; bridge routes are intentionally excluded",
            ))
    return results
