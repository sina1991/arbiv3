from decimal import Decimal as D

from arbitrage import evaluate_dex_cex, evaluate_dex_dex
from market_discovery import _decimal
from models import OrderBook, PoolCandidate, Token, Opportunity
from quote_engine import V2PoolState
from ranking import rank_opportunities


def pool(chain="eth", address="0xpool1", liquidity="100000", volume="50000"):
    return PoolCandidate(
        chain=chain,
        pool_address=address,
        dex_id="dex",
        dex_name="Test DEX",
        base_token=Token(chain, "0xbase", "ABC", decimals=18),
        quote_token=Token(chain, "0xquote", "USDT", decimals=6),
        liquidity_usd=D(liquidity),
        volume_24h_usd=D(volume),
        price_usd=D("1"),
    )


def test_v2_quote_accounts_for_fee_and_token_decimals():
    state = V2PoolState(
        token0="0xquote", token1="0xbase",
        reserve0=1_000_000_000, reserve1=1_000 * 10**18,
        decimals0=6, decimals1=18, fee_bps=30,
    )
    out = state.quote_exact_input("0xquote", "0xbase", D("100"))
    assert D("90") < out < D("91")
    reverse = state.quote_exact_input("0xbase", "0xquote", out)
    assert reverse < D("100")


def test_dex_cex_uses_orderbook_depth_and_fees():
    book = OrderBook(
        bids=((D("1.05"), D("60")), (D("1.04"), D("100"))),
        asks=((D("1.06"), D("100")),),
        observed_at=1.0,
    )
    candidate = pool()
    def quote(token_in, token_out, amount):
        if token_in == "0xquote" and token_out == "0xbase":
            return amount / D("1.02")
        if token_in == "0xbase" and token_out == "0xquote":
            return amount * D("1.02")
        raise ValueError("bad direction")

    rows = evaluate_dex_cex(candidate, quote, book, (D("100"),), D("0.001"), D("0"))
    assert len(rows) == 2
    assert all(row.budget_usdt == D("100") for row in rows)
    assert rows[0].route_type == "DEX_CEX"
    assert rows[0].quote_proceeds > 0


def test_cex_route_rejects_insufficient_visible_depth():
    book = OrderBook(
        bids=((D("1"), D("1")),),
        asks=((D("1.01"), D("1")),),
        observed_at=1.0,
    )
    candidate = pool()
    rows = evaluate_dex_cex(
        candidate, lambda a, b, amount: amount,
        book, (D("100"),), D("0"), D("0"),
    )
    assert rows == []


def test_dex_dex_only_compares_same_chain_and_same_pair():
    a, b = pool(address="0xa"), pool(address="0xb")
    rows = evaluate_dex_dex(
        a, b,
        lambda a, b, amount: amount / D("0.99"),
        lambda a, b, amount: amount * D("1.02"),
        (D("100"),), D("0"),
    )
    assert len(rows) == 2
    cross_chain = pool(chain="bsc", address="0xc")
    assert evaluate_dex_dex(
        a, cross_chain, lambda a, b, x: x, lambda a, b, x: x, (D("100"),), D("0")
    ) == []


def test_ranking_uses_profit_roi_and_max_profitable_budget():
    rows = [
        Opportunity("DEX_CEX", "route-a", "eth", "ABC", "USDT", D("100"), D("100"), D("110"),
                    D("10"), D("10"), D("0"), D("100000"), D("50000"), D("0"), True),
        Opportunity("DEX_CEX", "route-a", "eth", "ABC", "USDT", D("500"), D("500"), D("520"),
                    D("20"), D("4"), D("0"), D("100000"), D("50000"), D("0"), True),
        Opportunity("DEX_DEX", "route-b", "eth", "ABC", "USDT", D("100"), D("100"), D("106"),
                    D("6"), D("6"), D("0"), D("80000"), D("30000"), D("0"), True),
    ]
    ranked = rank_opportunities(rows)
    assert ranked
    assert ranked[0].max_profitable_capital_usdt in {D("100"), D("500")}
    assert ranked[0].score > 0


def test_decimal_parser_handles_missing_api_fields():
    assert _decimal(None) == D("0")
    assert _decimal("12.5") == D("12.5")
