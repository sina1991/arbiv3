# Multi-market DRY_RUN scanner

This module extends arbiV3 from a single Zilliqa pair into a configuration-driven, multi-chain market research and ranking system.

## Fixed research inputs

- Capital test sizes: **100, 250, 500, 1,000, 2,500 and 5,000 USDT**.
- Comparison routes: **DEX-CEX** and **DEX-DEX**.
- Discovery source: DexScreener search API. Discovery results are candidates, not verified pool facts.
- Supported chain registry: Zilliqa EVM, Ethereum, BNB Chain, Polygon, Arbitrum, Base, Optimism and Avalanche.
- Quote adapters: UniswapV2-compatible constant-product pools and explicitly configured UniswapV3-compatible QuoterV2 contracts.

## Market onboarding

Edit `market_registry.json`. Every market must include chain ID, pool address, token addresses and decimals, DEX/version, quote adapter, fee in the adapter's native units, and (for DEX-CEX) a verified CEX symbol plus an explicit base-asset mapping.

V2 fees are basis points. V3 fees are the pool's uint24 fee units (for example, 100 means 0.01%, not 100 bps). Never reuse one fee field across adapter types.

For V3, provide a QuoterV2 address only after verifying its deployment and ABI on the target chain. The scanner never uses a Router for quoting. For V2, the adapter reads pool reserves and calculates exact-input output using the configured fee.

Set an RPC environment variable for each chain you enable: `ZILLIQA_RPC_URL`, `ETHEREUM_RPC_URL`, `BSC_RPC_URL`, `POLYGON_RPC_URL`, `ARBITRUM_RPC_URL`, `BASE_RPC_URL`, `OPTIMISM_RPC_URL`, or `AVALANCHE_RPC_URL`.

## Discovery

`discover_dexscreener_pairs` filters candidates by allowed chain, minimum USD liquidity and 24h volume. Candidate data must be validated on-chain before adding it to the active registry. In particular verify token contracts, decimals, pool token ordering, pool code, reserves/liquidity, fee, quote contract and stablecoin redemption/peg assumptions.

## Ranking

Ranking is performed independently for each capital size. Only positive opportunities with a known gas estimate are rankable. The default score weights are:

- 45% normalized net profit
- 35% normalized ROI
- 20% normalized maximum profitable capital capacity

Gross profit, net profit, ROI, capacity and score must be reported separately. An unknown gas cost is not silently treated as zero. Quotes must be obtained at each capital size so price impact and pool depth are represented by the quote itself.

## Current WZIL/zUSDT reference market

The existing PlunderSwap V3 WZIL/zUSDT pool is retained as a reference entry but disabled in the multi-market registry. Previous exact-input/exact-output observations were inconsistent, and a verified QuoterV2 deployment plus the WZIL-to-KuCoin-ZIL asset mapping are not configured. It must not be ranked until those facts are independently validated.

## Safety

This project is a **read-only DRY_RUN market scanner**. It does not sign transactions, broadcast transactions, submit exchange orders, or request token approvals. Private keys and exchange trading credentials are not used by this scanner. Never add transaction-execution code to this project.

## Current implementation boundary

This commit provides the registry, discovery candidate filter, read-only V2 and QuoterV2 quote adapters, and per-capital ranking primitives. It does not claim every discovered pool is quoteable: each DEX deployment needs a validated adapter/configuration, each CEX needs a public market-data adapter, and every cross-chain route needs a separately modeled bridge/inventory cost before it can be compared as an economic opportunity.
