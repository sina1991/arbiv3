from decimal import Decimal as D
import asyncio
import json

import pytest
from web3 import Web3

from market_scanner import (
    CAPITAL_SIZES_USDT, V3PoolValidator, V3QuoterV2, V3PoolSnapshot, calculate_quote_capacity,
    evaluate_dex_cex, evaluate_dex_dex, from_raw, load_registry,
    rank_opportunities, to_raw, estimate_gas_cost_usdt,
    simulate_sell_base_into_bids, simulate_buy_base_with_quote,
)


def test_registry_is_v3_only_and_keeps_reference_disabled():
    registry=load_registry()
    assert registry["project_architecture"]=="V3_ONLY_CONCENTRATED_LIQUIDITY"
    assert [D(x) for x in registry["capital_sizes_usdt"]]==list(CAPITAL_SIZES_USDT)
    assert all(m["version"]=="V3" and m["quote_adapter"]=="v3_quoter_v2" for m in registry["markets"])
    assert all(not m["enabled"] for m in registry["markets"])
    assert all(c["status"]=="UNVERIFIED" for c in registry["chains"])


def test_amount_decimal_handling_is_exact():
    assert to_raw(D("1.25"),6)==1_250_000
    assert from_raw(1_250_000,6)==D("1.25")
    with pytest.raises(ValueError):
        to_raw(D("0.0000001"),6)


def test_capacity_is_quote_derived_not_maximum_sample_size():
    capacity=calculate_quote_capacity(
        lambda amount: amount*D("0.02")-amount*amount*D("0.00001"),
        minimum_input=D("1"),maximum_input=D("3000"),gas_cost=D("1"),tolerance=D("0.001"))
    assert D("100") < capacity < D("3000")
    assert calculate_quote_capacity(lambda amount:D("0"),minimum_input=D("1"),
                                    maximum_input=D("100"),gas_cost=D("0"))==0


def test_unknown_gas_never_produces_net_or_rankable_opportunity():
    rows=evaluate_dex_cex(
        market_id="pool",capital_sizes_usdt=[D("100")],
        quote_to_base=lambda amount:amount/D("10"),
        base_to_quote=lambda amount:amount*D("10.2"),
        cex_bid=D("10.2"),cex_ask=D("10.3"),cex_fee_rate=D(".001"),
        quote_asset_usd_price=D("1"),gas_cost_usdt=None,
        status="VERIFIED",asset_mapping_verified=True,usd_prices_verified=True,cex_depth_verified=True,
        cex_sell_base=lambda base:base*D("10.2"),
        cex_buy_base_with_quote=lambda quote:quote/D("10.3"),slippage_bps=30)
    assert len(rows)==2
    assert all(row["net_profit_usdt"] is None for row in rows)
    assert all(row["status"]=="INSUFFICIENT_DATA" for row in rows)
    assert rank_opportunities(rows,{"net_profit":".45","roi":".35","capital_capacity":".20"})==[]


def test_unverified_asset_mapping_is_never_compared():
    rows=evaluate_dex_cex(
        market_id="pool",capital_sizes_usdt=[D("100")],
        quote_to_base=lambda amount:amount,base_to_quote=lambda amount:amount,
        cex_bid=D("1"),cex_ask=D("1"),cex_fee_rate=D("0"),
        quote_asset_usd_price=D("1"),gas_cost_usdt=D("0"),
        status="UNVERIFIED",asset_mapping_verified=False)
    assert rows[0]["status"]=="UNVERIFIED"


def test_dex_dex_rejects_cross_chain_or_nonidentical_token_contracts():
    a={"id":"a","chain_id":1,"status":"VERIFIED","enabled":True,"token_base":{"address":"0x"+"11"*20,"decimals":18},
       "token_quote":{"address":"0x"+"22"*20,"decimals":6}}
    b={"id":"b","chain_id":56,"status":"VERIFIED","enabled":True,"token_base":{"address":"0x"+"11"*20,"decimals":18},
       "token_quote":{"address":"0x"+"22"*20,"decimals":6}}
    rows=evaluate_dex_dex(market_a=a,market_b=b,capital_sizes_usdt=[D("100")],
        a_quote_to_base=lambda x:x,a_base_to_quote=lambda x:x,
        b_quote_to_base=lambda x:x,b_base_to_quote=lambda x:x,
        quote_asset_usd_price=D("1"),gas_cost_a_usdt=D("1"),gas_cost_b_usdt=D("1"),usd_prices_verified=True,slippage_bps=30)
    assert rows[0]["status"]=="REJECTED"


def test_dex_dex_unknown_one_leg_gas_is_insufficient_data():
    a={"id":"a","chain_id":1,"status":"VERIFIED","enabled":True,"token_base":{"address":"0x"+"11"*20,"decimals":18},
       "token_quote":{"address":"0x"+"22"*20,"decimals":6}}
    b={"id":"b","chain_id":1,"status":"VERIFIED","enabled":True,"token_base":{"address":"0x"+"11"*20,"decimals":18},
       "token_quote":{"address":"0x"+"22"*20,"decimals":6}}
    rows=evaluate_dex_dex(market_a=a,market_b=b,capital_sizes_usdt=[D("100")],
        a_quote_to_base=lambda x:x/D("10"),a_base_to_quote=lambda x:x*D("10"),
        b_quote_to_base=lambda x:x/D("9"),b_base_to_quote=lambda x:x*D("11"),
        quote_asset_usd_price=D("1"),gas_cost_a_usdt=D(".1"),gas_cost_b_usdt=None,usd_prices_verified=True,slippage_bps=30)
    assert len(rows)==2
    assert all(row["net_profit_usdt"] is None for row in rows)
    assert all(row["status"]=="INSUFFICIENT_DATA" for row in rows)


def test_ranking_requires_verified_complete_rows_and_uses_weights():
    weights={"net_profit":".45","roi":".35","capital_capacity":".20"}
    base={"route":"DEX_CEX","market_a":"a","market_b":None,"capital_usdt":"100",
          "gross_profit_usdt":"3","gas_cost_usdt":".1","net_profit_usdt":"2.9",
          "roi_percent":"2.9","capacity_usdt":"500","status":"VERIFIED"}
    rows=[base,{**base,"market_a":"b","status":"UNVERIFIED"},
          {**base,"market_a":"c","capacity_usdt":None}]
    ranked=rank_opportunities(rows,weights)
    assert len(ranked)==1 and ranked[0].market_a=="a" and ranked[0].rank==1


class FakeCall:
    def __init__(self,value): self.value=value
    def call(self): return self.value


class FakeFunctions:
    def __init__(self,values): self.values=values
    def __getattr__(self,name): return lambda *args:FakeCall(self.values[name])


class FakeContract:
    def __init__(self,values): self.functions=FakeFunctions(values)


class FakeEth:
    chain_id=1
    def __init__(self,contracts): self.contracts=contracts
    def get_code(self,address): return b"\x01"
    def contract(self,address,abi): return self.contracts[address.lower()]
    def get_block(self,block): return {"number":123,"timestamp":1_700_000_000}


class FakeWeb3:
    def __init__(self,eth): self.eth=eth


def test_v3_factory_pool_token_fee_tick_liquidity_validation_with_mock_rpc():
    factory=Web3.to_checksum_address("0x"+"aa"*20)
    pool=Web3.to_checksum_address("0x"+"bb"*20)
    token0=Web3.to_checksum_address("0x"+"11"*20)
    token1=Web3.to_checksum_address("0x"+"22"*20)
    contracts={
      factory.lower():FakeContract({"getPool":pool,"feeAmountTickSpacing":10}),
      pool.lower():FakeContract({"factory":factory,"token0":token0,"token1":token1,
        "fee":500,"tickSpacing":10,"slot0":(2**96,0,0,0,0,0,True),"liquidity":1000}),
      token0.lower():FakeContract({"decimals":18,"symbol":"A"}),
      token1.lower():FakeContract({"decimals":6,"symbol":"B"}),
    }
    snapshot=V3PoolValidator(FakeWeb3(FakeEth(contracts))).validate(
      expected_chain_id=1,factory_address=factory,pool_address=pool,
      token_a=token0,token_b=token1,fee=500)
    assert snapshot.status=="VERIFIED"
    assert snapshot.token0==token0 and snapshot.token1==token1
    assert snapshot.token0_decimals==18 and snapshot.token1_decimals==6
    assert snapshot.fee==500 and snapshot.tick_spacing==10
    assert snapshot.liquidity==1000 and snapshot.block_number==123


def test_v3_validator_rejects_wrong_factory_mapping():
    factory=Web3.to_checksum_address("0x"+"aa"*20)
    pool=Web3.to_checksum_address("0x"+"bb"*20)
    token0=Web3.to_checksum_address("0x"+"11"*20)
    token1=Web3.to_checksum_address("0x"+"22"*20)
    other=Web3.to_checksum_address("0x"+"cc"*20)
    contracts={
      factory.lower():FakeContract({"getPool":other,"feeAmountTickSpacing":10}),
      pool.lower():FakeContract({"factory":factory,"token0":token0,"token1":token1,
        "fee":500,"tickSpacing":10,"slot0":(2**96,0,0,0,0,0,True),"liquidity":1000}),
      token0.lower():FakeContract({"decimals":18,"symbol":"A"}),
      token1.lower():FakeContract({"decimals":6,"symbol":"B"}),
    }
    with pytest.raises(ValueError,match="Factory getPool"):
      V3PoolValidator(FakeWeb3(FakeEth(contracts))).validate(
        expected_chain_id=1,factory_address=factory,pool_address=pool,
        token_a=token0,token_b=token1,fee=500)



def test_gas_cost_conversion_requires_real_positive_inputs():
    cost=estimate_gas_cost_usdt(200_000,30_000_000_000,D("3000"))
    assert cost==D("18")
    with pytest.raises(ValueError):
        estimate_gas_cost_usdt(0,30_000_000_000,D("3000"))


def test_quote_grid_requests_six_sizes_in_both_directions():
    from market_scanner import V3PoolSnapshot, V3Quote, quote_capital_grid
    class FakeQuoter:
        def quote(self,snapshot,token_in,amount_in):
            token_out=snapshot.token1 if token_in==snapshot.token0 else snapshot.token0
            return V3Quote(token_in,token_out,amount_in,amount_in*D("2"),D("2"),D("2"),
                           D("0"),0,100000,123,"2026-01-01T00:00:00+00:00",500,0,2**96)
    snapshot=V3PoolSnapshot(1,"factory","pool","token0","token1",18,6,500,10,
                            2**96,0,1000,True,123,"2026-01-01T00:00:00+00:00")
    rows=quote_capital_grid(snapshot,FakeQuoter(),capital_sizes_usdt=CAPITAL_SIZES_USDT,
                            token0_usd_price=D("1"),token1_usd_price=D("1"))
    assert len(rows)==12
    assert len({row["capital_usdt"] for row in rows})==6
    assert {row["direction"] for row in rows}=={"token0->token1","token1->token0"}


def test_dex_cex_requires_verified_orderbook_depth_and_usd_prices():
    rows=evaluate_dex_cex(
        market_id="pool",capital_sizes_usdt=[D("100")],
        quote_to_base=lambda amount:amount,base_to_quote=lambda amount:amount,
        cex_bid=D("1"),cex_ask=D("1"),cex_fee_rate=D("0"),
        quote_asset_usd_price=D("1"),gas_cost_usdt=D("0"),
        status="VERIFIED",asset_mapping_verified=True)
    assert rows[0]["status"]=="INSUFFICIENT_DATA"


def test_cex_orderbook_depth_simulation_consumes_multiple_levels():
    bids=[(D("10"),D("2")),(D("9"),D("3"))]
    asks=[(D("10"),D("2")),(D("11"),D("3"))]
    assert simulate_sell_base_into_bids(D("3"),bids)==D("29")
    assert simulate_buy_base_with_quote(D("25"),asks)==D("2")+D("5")/D("11")
    with pytest.raises(ValueError,match="Insufficient"):
        simulate_sell_base_into_bids(D("6"),bids)


def test_quoter_v2_mock_returns_v3_quote_context():
    quoter_address=Web3.to_checksum_address("0x"+"dd"*20)
    token0=Web3.to_checksum_address("0x"+"11"*20)
    token1=Web3.to_checksum_address("0x"+"22"*20)
    contracts={quoter_address.lower():FakeContract({
        "quoteExactInputSingle":(10**18,2**96,3,150_000)})}
    quoter=V3QuoterV2(FakeWeb3(FakeEth(contracts)),quoter_address)
    snapshot=V3PoolSnapshot(1,"factory","pool",token0,token1,18,6,500,10,
                            2**96,0,10**12,True,123,"2026-01-01T00:00:00+00:00")
    quote=quoter.quote(snapshot,token0,D("1"))
    assert quote.amount_in==D("1")
    assert quote.amount_out==D("1000000000000")
    assert quote.initialized_ticks_crossed==3
    assert quote.gas_estimate==150_000
    assert quote.fee_units==500
    assert quote.block_number==123


def test_registry_rejects_v2_adapter(tmp_path):
    data=load_registry()
    data["markets"][0]["version"]="V2"
    path=tmp_path/"registry.json"
    path.write_text(json.dumps(data),encoding="utf-8")
    with pytest.raises(ValueError,match="Only V3"):
        load_registry(path)


def test_registry_rejects_duplicate_pool_on_same_chain(tmp_path):
    data=load_registry()
    duplicate=dict(data["markets"][0])
    duplicate["id"]="duplicate"
    data["markets"].append(duplicate)
    path=tmp_path/"registry.json"
    path.write_text(json.dumps(data),encoding="utf-8")
    with pytest.raises(ValueError,match="Duplicate V3 pool"):
        load_registry(path)


def test_capacity_search_does_not_hide_rpc_errors():
    with pytest.raises(ConnectionError):
        calculate_quote_capacity(lambda amount: (_ for _ in ()).throw(ConnectionError("RPC down")),
            minimum_input=D("1"),maximum_input=D("10"),gas_cost=D("1"))


def test_integrated_scan_emits_reports_without_enabled_markets(tmp_path):
    from scan_engine import scan_registry
    registry=load_registry()
    chains={int(chain["id"]):chain for chain in registry["chains"]}
    report=asyncio.run(scan_registry(registry,chains,str(tmp_path)))
    assert report["status"]=="INSUFFICIENT_DATA"
    assert report["verified_market_count"]==0
    assert (tmp_path/"scan_report.json").exists()
    assert (tmp_path/"scan_report.csv").exists()
