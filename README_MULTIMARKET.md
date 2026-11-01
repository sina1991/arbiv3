# V3-only market onboarding and validation

arbiV3 is intentionally a **V3 concentrated-liquidity system**. Do not add V2 adapters, constant-product math, getReserves quoting or a generic V2/V3 abstraction.

## Required market metadata

Each enabled market must specify:
- chain ID and RPC environment variable
- V3 DEX identifier and protocol version
- V3 Factory, Pool and QuoterV2 addresses
- token addresses and independently verified decimals
- pool fee in native uint24 V3 fee units
- token USD price sources when USD-denominated capital sizing is required
- CEX symbol and independently verified asset mapping for DEX-CEX
- gas estimation inputs and liquidity-backed capacity search bound

A market is enabled only when Factory.getPool(tokenA, tokenB, fee) returns the configured pool, pool.factory matches the factory, token0/token1 and fee match, slot0/liquidity are valid, and the configured QuoterV2 successfully quotes both directions with the expected ABI.

## Status meanings

- `VERIFIED`: all required on-chain and economic inputs for the specific operation are present and validated.
- `UNVERIFIED`: deployment or asset facts are not independently established.
- `REJECTED`: incompatible tokens/chains, invalid contract mapping, invalid pool state or failed validation.
- `INSUFFICIENT_DATA`: required gas, USD conversion, quote or capacity inputs are missing.

A chain name in the registry is not proof of support. A successful RPC check is not proof of a V3 DEX deployment. A successful Quoter call is not proof of profitable execution.

## Discovery

DexScreener is a candidate source only. It does not establish protocol version, factory membership, fee, token decimals or quote validity. Validate every candidate on-chain before adding it as an enabled market. Deduplicate by (chain ID, pool address).

## Quoting

The required capital inputs are 100, 250, 500, 1,000, 2,500 and 5,000 USDT. For each size, quote both token directions using the V3 QuoterV2 belonging to that chain and DEX. Convert USD capital into token units only with independently verified USD prices. Record effective price, spot reference, price impact, fee tier, tick, initialized ticks crossed, liquidity, block number and timestamp.

## Costs and capacity

Gas USD cost is derived from explicit gas units, current gas price and native-token USD price. Missing inputs must remain null. Capacity must be measured by actual quotes over a liquidity-backed search range; do not substitute the largest profitable test size. The capacity search is an estimate bounded by its configured search interval and should be reported with those bounds.

## Ranking and reporting

Only fully verified positive-net opportunities with measured capacity are eligible. The score weights are 45% net profit, 35% ROI and 20% capital capacity. JSON and CSV reports must preserve status and rejection/insufficient-data reasons.

## PlunderSwap WZIL/zUSDT

The reference pool remains disabled. Official PlunderSwap developer documentation provides Factory and QuoterV2 deployment addresses; they are configured as candidates but are not yet independently validated against the current RPC. The router is reference metadata only, not a quote contract. KuCoin ZIL-USDT is not proof that WZIL is interchangeable with ZIL.

## Safety

The scanner is read-only. It must never sign or broadcast transactions, request approvals, submit KuCoin orders or load private keys.
