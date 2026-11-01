# arbiV3 — Multi-market DRY_RUN scanner

arbiV3 is a read-only research tool for evaluating DEX-CEX and DEX-DEX arbitrage candidates across multiple EVM networks. The original WZIL/zUSDT PlunderSwap V3 market is retained as a reference, not assumed to be valid.

## Fixed research configuration

- Capital test sizes: 100, 250, 500, 1,000, 2,500 and 5,000 USDT.
- Comparison routes: DEX-CEX and DEX-DEX.
- Chain registry: Zilliqa EVM, Ethereum, BNB Chain, Polygon, Arbitrum, Base, Optimism and Avalanche.
- Discovery source: DexScreener public search API.
- Quote adapters: UniswapV2-compatible constant-product pools and explicitly configured UniswapV3-compatible QuoterV2 contracts.
- Ranking weights: 45% net profit, 35% ROI and 20% maximum profitable capital capacity.

## Read-only guarantee

This project has no transaction signing, transaction broadcast, token approval, private exchange API, or order-submission implementation. It does not need a wallet private key or KuCoin API credentials. KuCoin access is limited to public ticker data.

## Setup

Python 3.11+:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Inspect configured markets

```bash
python main.py
```

## Discover candidate pools

For example:

```bash
python main.py --discover USDT
python main.py --discover USDC --min-liquidity 25000 --min-volume 50000
```

Discovery output is a candidate list only. It is not an on-chain validation and is not an arbitrage ranking. Verify chain ID, pool bytecode, token addresses and decimals, token ordering, fee, liquidity/reserves, quote adapter deployment, and the corresponding CEX asset mapping before enabling a market.

## Market registry and quote adapters

See [README_MULTIMARKET.md](README_MULTIMARKET.md) and [market_registry.json](market_registry.json). V2 fees use basis points. V3 fees use the pool's uint24 fee units. They are deliberately separate to avoid the common 100-versus-100-bps configuration error.

A V3 market requires a verified QuoterV2 address and compatible ABI. The scanner does not use the swap Router for quotes. Unknown gas cost is never treated as zero: an opportunity without a configured/derived gas estimate is excluded from net-profit ranking.

## Current WZIL/zUSDT reference

The original PlunderSwap V3 WZIL/zUSDT pool is disabled in the multi-market registry. Earlier exact-input and exact-output observations were inconsistent, and neither a verified QuoterV2 deployment nor the WZIL-to-KuCoin-ZIL asset mapping has been established. The market must remain unranked until those checks pass.

## Tests

```bash
python -m compileall -q .
python -m pytest -q
```
