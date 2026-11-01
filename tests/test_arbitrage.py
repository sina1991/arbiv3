from decimal import Decimal as D

from arbitrage import evaluate_opportunities
from models import Direction


def test_buy_dex_sell_cex_profit_includes_fees_and_gas():
    rows = evaluate_opportunities(
        sizes=(D("100"),), dex_exact_input_sell={},
        dex_exact_output_buy={D("100"): D("9.90")},
        kucoin_bid=D("0.101"), kucoin_ask=D("0.102"),
        kucoin_fee=D("0.001"), minimum_profit=D("0.01"),
        gas_cost_usdt=D("0.02"),
    )
    assert rows[0].direction is Direction.BUY_WZIL_ON_DEX_SELL_ZIL_ON_CEX
    assert rows[0].estimated_profit_usdt == D("0.1699")
    assert rows[0].executable


def test_buy_cex_sell_dex_profit_includes_fees_and_gas():
    rows = evaluate_opportunities(
        sizes=(D("100"),), dex_exact_input_sell={D("100"): D("10.5")},
        dex_exact_output_buy={}, kucoin_bid=D("0.1"), kucoin_ask=D("0.1"),
        kucoin_fee=D("0.001"), minimum_profit=D("0.01"),
        gas_cost_usdt=D("0.02"),
    )
    assert rows[0].direction is Direction.BUY_ZIL_ON_CEX_SELL_WZIL_ON_DEX
    assert rows[0].estimated_profit_usdt == D("0.47")
    assert rows[0].executable


def test_missing_gas_cost_never_marks_opportunity_executable():
    rows = evaluate_opportunities(
        sizes=(D("100"),), dex_exact_input_sell={D("100"): D("10.5")},
        dex_exact_output_buy={}, kucoin_bid=D("0.1"), kucoin_ask=D("0.1"),
        kucoin_fee=D("0"), minimum_profit=D("0.01"), gas_cost_usdt=None,
    )
    assert not rows[0].executable
    assert rows[0].estimated_profit_usdt is None
    assert "gas conversion unavailable" in rows[0].reason
