# arbiV3

A standalone ZIL-USDT cross-exchange arbitrage bot using **PlunderSwap V3 only** and KuCoin spot markets.

## Scope

- DEX market: WZIL / zUSDT on the configured PlunderSwap V3 pool.
- CEX market: ZIL-USDT on KuCoin.
- Supported routes:
  - Buy WZIL on PlunderSwap V3 (exact output), sell ZIL on KuCoin.
  - Buy ZIL on KuCoin, sell WZIL on PlunderSwap V3 (exact input).
- No V2 implementation, V2 pool, WZIL/zUSDC market, reserve-based quote math, or V2 ABI is part of this repository.

## Safety modes

The default is `DRY_RUN`. It reads market data, obtains V3 quotes by calling the configured V3 router through `eth_call`, evaluates balances and estimated profitability, and logs candidate trades. It does not sign or broadcast blockchain transactions and does not submit KuCoin orders.

Real trading requires both:
```dotenv
TRADING_MODE=LIVE
LIVE_TRADING_CONFIRM=I_UNDERSTAND_REAL_ORDERS
```
Live mode can submit real orders and spend real assets. Start with DRY_RUN and verify the pool, router, token addresses, decimals, fee tier, wallet, allowances, and KuCoin account before enabling it.

## Important execution model

This is pre-funded cross-exchange arbitrage: WZIL and zUSDT must already be available in the PlunderSwap wallet, while ZIL and USDT must already be available in KuCoin. The bot does not transfer assets between venues.

The two venue trades are not atomic. A successful trade on one venue followed by a failure on the other can leave inventory unbalanced. Live execution therefore verifies KuCoin order fills and on-chain receipts, but cannot guarantee a hedge if the second venue fails. Use conservative sizes and monitor inventory.

## V3 quoting

Quotes are requested from the PlunderSwap V3 router using read-only `eth_call` against `exactInputSingle` and `exactOutputSingle`. This delegates tick traversal and pool fee application to the deployed contract rather than approximating a swap from the current `sqrtPriceX96` and active liquidity. A quote can fail if the wallet lacks the required token balance/allowance or if the router/pool configuration is wrong; failures are treated as unavailable quotes, never as zero-cost opportunities.

## Setup

Python 3.11+

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in the RPC endpoint, KuCoin credentials, wallet address, and (only for LIVE) private key. Never commit `.env`.

## Run

```bash
python main.py
```

Set `TRADING_MODE=DRY_RUN` for safe observation. The bot refuses LIVE mode unless the explicit confirmation variable is also set.

## Configuration notes

- The configured PlunderSwap V3 fee is expressed in Uniswap-style fee units (1,000,000 = 100%), not basis points. The default `100` corresponds to 0.01%; verify this against the deployed pool before use.
- WZIL uses 18 decimals and zUSDT uses 6 decimals in the current configuration; verify on-chain.
- The KuCoin market order uses ZIL quantity. The implementation checks final order status and filled size rather than treating order acceptance as a completed fill.
- Profit estimates include configured KuCoin fee and estimated PlunderSwap gas converted to USDT only when a gas-to-USDT estimate is configured. If unavailable, the bot marks gas-adjusted profit as incomplete and will not call the estimate guaranteed net profit.

## Tests

```bash
pytest -q
```

Tests cover pure arbitrage arithmetic and safety-mode behavior. They do not replace fork testing or small-value live validation.
