# arbiV3 — V3-only multi-chain arbitrage research scanner

arbiV3 is a **Uniswap V3 / V3-compatible concentrated-liquidity** arbitrage scanner. Its core supports V3 factories, V3 pools, fee tiers, tick state and QuoterV2. V2 constant-product pools, reserve-based quoting and hybrid V2/V3 routing are explicitly out of scope.

## Safety and operating mode

- Read-only: no transaction signing, broadcasting, approvals or exchange orders.
- KuCoin integration is public market data only.
- Never enable a market without validating its chain, factory, pool, token metadata, fee, QuoterV2 ABI/deployment and successful quotes.
- DexScreener is discovery-only. Candidate pairs remain `UNVERIFIED` until checked on-chain.
- Unknown gas, token USD price, CEX asset mapping or liquidity capacity means no net profit and no ranking.
- Cross-chain opportunities are excluded unless a separately validated bridge/inventory model is added.

## Networks

The registry contains Zilliqa EVM (32769), Ethereum (1), BNB Chain (56), Polygon (137), Arbitrum (42161), Base (8453), Optimism (10) and Avalanche (43114). Registry presence does not mean operational support. Each chain requires working RPC and matching chain ID, then separately verified V3 deployments.

## Install

Python 3.11+:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Commands

Inspect registry and emit JSON/CSV reports:

```bash
python main.py
```

Discover candidate markets (not verified or rankable):

```bash
python main.py --discover USDT
python main.py --discover USDC --min-liquidity 25000 --min-volume 50000
```

Validate a configured V3 market's RPC, chain ID, factory getPool mapping, pool factory/token ordering/fee/tickSpacing/slot0/liquidity, ERC-20 decimals, QuoterV2 bytecode and bidirectional probe quotes:

```bash
python main.py --validate plunderswap_v3_wzil_zusdt_reference
python main.py --check-networks
```

Validation outputs are written to `reports/` in JSON and CSV. A successful deployment probe alone does not prove token USD parity, gas conversion, CEX mapping or economic profitability.

## V3 architecture

- `V3PoolValidator`: chain ID, deployed contracts, Factory.getPool, pool.factory, token0/token1, decimals, fee, tickSpacing, slot0, active liquidity and block timestamp.
- `V3QuoterV2`: V3 QuoterV2 exact-input quote with output, post-swap sqrtPriceX96, initialized ticks crossed and gas estimate.
- `quote_capital_grid`: six USD-equivalent capital sizes in both directions, requiring independent USD prices for both tokens.
- `calculate_quote_capacity`: quote-driven search over a supplied liquidity-backed capital bound; it is not the maximum profitable sample size.
- `evaluate_dex_cex` and `evaluate_dex_dex`: both directions, explicit token compatibility, fees and gas inputs.
- `rank_opportunities`: only VERIFIED, fully costed opportunities with measured capacity are eligible; weights are 45% net profit, 35% ROI, 20% capital capacity.

## PlunderSwap reference

The WZIL/zUSDT market on Zilliqa remains `UNVERIFIED`, disabled and reference-only. Known Router, Pool, token addresses and fee are retained as reference metadata. Factory and QuoterV2 are deliberately unset. WZIL and KuCoin ZIL are not assumed equivalent. No ranking is allowed until all independent checks pass.

## Tests

```bash
python -m compileall -q .
python -m pytest -q
```

Mock tests do not prove a live deployment. Integration tests must be run only against verified deployments. No secrets belong in the repository.
