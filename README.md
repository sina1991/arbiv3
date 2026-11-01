# arbiV3 — Multi-Market Arbitrage Research Scanner

A **DRY_RUN-only** market discovery and arbitrage research framework. It is designed to test the same pre-funded arbitrage model across multiple EVM networks, DEX pools and centralized-exchange order books, then rank opportunities by profit and tested capital capacity.

**This project has no trading capability.** It contains no wallet private-key configuration, transaction signing, transaction broadcasting, token approvals, DEX swap submission, or CEX order submission.

## Requested capital points

The scanner uses these quote-currency budgets by default:

`100, 250, 500, 1000, 2500, 5000 USDT`

Change them with `TRADE_SIZES_USDT`. Both comparison families are supported by the pure evaluation engine:

- **DEX-CEX:** DEX buy → CEX sell, and CEX buy → DEX sell.
- **DEX-DEX:** buy on one pool → sell on another pool for the same pair on the same chain.

Cross-chain pools are discovered independently. Cross-chain arbitrage is not ranked as a tradable route because bridge costs, transfer latency, inventory availability and settlement risk are not modeled.

## Current implementation status

1. GeckoTerminal discovery across configured networks.
2. Pool metadata normalization: chain, DEX, token addresses/symbols, liquidity and 24-hour volume.
3. Liquidity and quote-token candidate filters.
4. Exact-input quote interface with a constant-product V2 implementation.
5. Read-only Uniswap V3-compatible QuoterV2 adapter. The Quoter address and fee must be verified and explicitly configured for each deployment; no addresses are guessed.
6. DEX-CEX evaluation using CEX order-book depth rather than only best bid/ask.
7. Same-chain DEX-DEX evaluation using exact-size quotes.
8. Transparent ranking based on normalized net profit (55%), tested profitable capital capacity (30%) and ROI (15%).

The first scanner run is **discovery-first**. It writes `reports/latest-markets.json`. It does not invent profit scores from spot prices. A market enters ranking only after its RPC, pool, token decimals, fee model, quote adapter and CEX symbol mapping have been verified and connected to the quote engine.

## Reference market

The current PlunderSwap V3 WZIL/zUSDT market remains in `markets.example.json` as the reference configuration. It is deliberately disabled for ranking until the Quoter and exact-input behavior are verified. The existing router is not used as a quote engine.

## Setup

Python 3.11+:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configure `SCANNER_NETWORKS`, capital sizes and discovery filters in `.env`. Add only verified RPC endpoints and market configurations to the local configuration. Never commit credentials or private keys.

## Run

```bash
python main.py
```

The discovery report is written to `reports/latest-markets.json`. The scanner logs network-level discovery failures without treating a failed network as an empty market.

## Quote and ranking rules

- Pool reserve/liquidity and 24-hour volume are discovery filters, not executable depth.
- V2 quotes use the configured pool reserves, token decimals and explicitly supplied fee in basis points.
- V3 quotes use a deployment-verified QuoterV2 address and the pool's actual V3 fee units.
- CEX fills are simulated against visible order-book levels and stop when the requested size exceeds displayed depth.
- DEX-DEX routes are restricted to pools with the same base/quote token addresses on the same chain.
- Gas must be supplied as a verified estimate in the same quote currency before an opportunity is considered complete.
- zUSDT is not assumed to be equivalent to USDT without an explicit depeg/price validation policy.

## Tests

```bash
python -m compileall -q .
python -m pytest -q
```

Tests cover quote arithmetic, order-book depth simulation, route calculations, capital-size ranking and discovery normalization. They do not certify any live market or external contract deployment.
