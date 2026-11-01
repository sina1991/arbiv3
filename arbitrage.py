from decimal import Decimal
from models import Direction, Opportunity


def evaluate_opportunities(
    sizes: tuple[Decimal, ...],
    dex_exact_input_sell: dict[Decimal, Decimal],
    dex_exact_output_buy: dict[Decimal, Decimal],
    kucoin_bid: Decimal,
    kucoin_ask: Decimal,
    kucoin_fee: Decimal,
    minimum_profit: Decimal,
    gas_cost_usdt: Decimal | None,
) -> list[Opportunity]:
    """Evaluate pre-funded cross-venue routes using V3 quotes and KuCoin top-of-book."""
    opportunities: list[Opportunity] = []
    for size in sizes:
        # Buy WZIL on V3 (spend zUSDT), then sell ZIL on KuCoin.
        buy_cost = dex_exact_output_buy.get(size)
        if buy_cost is not None:
            proceeds = size * kucoin_bid * (Decimal("1") - kucoin_fee)
            raw_profit = proceeds - buy_cost
            net_profit = raw_profit - gas_cost_usdt if gas_cost_usdt is not None else raw_profit
            complete = gas_cost_usdt is not None
            opportunities.append(Opportunity(
                Direction.BUY_WZIL_ON_DEX_SELL_ZIL_ON_CEX, size, buy_cost,
                Decimal("0"), Decimal("0"), proceeds, net_profit, gas_cost_usdt,
                complete and net_profit >= minimum_profit,
                "gas-adjusted estimate" if complete else "gas conversion unavailable; not executable"
            ))

        # Buy ZIL on KuCoin, then sell pre-funded WZIL on V3.
        sell_proceeds = dex_exact_input_sell.get(size)
        if sell_proceeds is not None:
            cex_cost = size * kucoin_ask * (Decimal("1") + kucoin_fee)
            raw_profit = sell_proceeds - cex_cost
            net_profit = raw_profit - gas_cost_usdt if gas_cost_usdt is not None else raw_profit
            complete = gas_cost_usdt is not None
            opportunities.append(Opportunity(
                Direction.BUY_ZIL_ON_CEX_SELL_WZIL_ON_DEX, size, Decimal("0"),
                sell_proceeds, cex_cost, Decimal("0"), net_profit, gas_cost_usdt,
                complete and net_profit >= minimum_profit,
                "gas-adjusted estimate" if complete else "gas conversion unavailable; not executable"
            ))
    return sorted(opportunities, key=lambda item: item.estimated_profit_usdt, reverse=True)
