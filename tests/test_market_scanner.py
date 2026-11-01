from decimal import Decimal as D

import pytest

from market_scanner import load_registry, rank_opportunities, V2PoolQuoter


def test_registry_contains_requested_capital_sizes_and_both_routes():
    registry = load_registry()
    assert registry["capital_sizes_usdt"] == ["100", "250", "500", "1000", "2500", "5000"]
    assert registry["comparison_routes"] == ["DEX_CEX", "DEX_DEX"]


def test_ranking_is_per_capital_and_uses_net_profit_roi_and_capacity():
    weights = {"net_profit": "0.45", "roi": "0.35", "capital_capacity": "0.20"}
    rows = [
        {"route": "DEX_CEX", "market_a": "A", "capital_usdt": "100",
         "gross_profit_usdt": "2", "gas_cost_usdt": "0.2", "net_profit_usdt": "1.8",
         "roi_percent": "1.8", "capacity_usdt": "500"},
        {"route": "DEX_DEX", "market_a": "B", "market_b": "C", "capital_usdt": "100",
         "gross_profit_usdt": "3", "gas_cost_usdt": "0.1", "net_profit_usdt": "2.9",
         "roi_percent": "2.9", "capacity_usdt": "1000"},
        {"route": "DEX_CEX", "market_a": "D", "capital_usdt": "250",
         "gross_profit_usdt": "5", "gas_cost_usdt": None, "net_profit_usdt": None,
         "roi_percent": None, "capacity_usdt": None},
    ]
    ranked = rank_opportunities(rows, weights)
    assert len(ranked) == 2
    assert ranked[0].capital_usdt == D("100")
    assert ranked[0].market_a == "B"
    assert ranked[0].rank == 1
    assert ranked[0].score <= D("100")


def test_unpriced_gas_and_nonpositive_net_profit_are_not_ranked():
    rows = [
        {"route": "DEX_CEX", "market_a": "A", "capital_usdt": "100",
         "gross_profit_usdt": "4", "gas_cost_usdt": None, "net_profit_usdt": None,
         "roi_percent": None, "capacity_usdt": None},
        {"route": "DEX_DEX", "market_a": "B", "market_b": "C", "capital_usdt": "100",
         "gross_profit_usdt": "1", "gas_cost_usdt": "0.2", "net_profit_usdt": "-0.1",
         "roi_percent": "-0.1", "capacity_usdt": "100"},
    ]
    assert rank_opportunities(rows, {"net_profit": ".45", "roi": ".35", "capital_capacity": ".20"}) == []


def test_v2_fee_units_reject_invalid_basis_points():
    with pytest.raises(ValueError, match="fee_bps"):
        V2PoolQuoter(None, "0x0000000000000000000000000000000000000001", 10001)


def test_dex_cex_evaluator_models_both_directions_and_unknown_gas():
    from market_scanner import evaluate_dex_cex
    rows = evaluate_dex_cex(
        "pool-a", [D("100")],
        quote_to_base=lambda quote: quote / D("10"),
        base_to_quote=lambda base: base * D("10.2"),
        cex_bid=D("10.2"), cex_ask=D("10.3"), cex_fee_rate=D("0.001"),
        quote_asset_usd_price=D("1"), gas_cost_usdt=None,
    )
    assert {row["route"] for row in rows} == {"DEX_CEX", "CEX_DEX"}
    assert all(row["net_profit_usdt"] is None for row in rows)
    assert all(row["capacity_usdt"] is None for row in rows)


def test_dex_dex_evaluator_subtracts_both_network_legs_gas():
    from market_scanner import evaluate_dex_dex
    rows = evaluate_dex_dex(
        "pool-a", "pool-b", [D("100")],
        a_quote_to_base=lambda quote: quote / D("10"),
        a_base_to_quote=lambda base: base * D("10"),
        b_quote_to_base=lambda quote: quote / D("10.2"),
        b_base_to_quote=lambda base: base * D("10.3"),
        quote_asset_usd_price=D("1"),
        gas_cost_a_usdt=D("0.1"), gas_cost_b_usdt=D("0.1"),
    )
    profitable = [row for row in rows if row["net_profit_usdt"] is not None and row["net_profit_usdt"] > 0]
    assert profitable
    assert all(row["gas_cost_usdt"] == D("0.2") for row in rows)
