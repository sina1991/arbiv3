# arbiV3

A standalone ZIL-USDT cross-exchange arbitrage bot using **PlunderSwap V3 only** and KuCoin spot markets.

## Supported market and routes

- DEX market: WZIL / zUSDT on the configured PlunderSwap V3 pool.
- CEX market: ZIL-USDT on KuCoin.
- Buy WZIL on PlunderSwap V3 (exact output), then sell ZIL on KuCoin.
- Buy ZIL on KuCoin, then sell WZIL on PlunderSwap V3 (exact input).

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

The two venue trades are not atomic. A successful trade on one venue followed by a failure on the other can leave inventory unbalanced. Live execution verifies KuCoin order fills and on-chain receipts; if the second leg fails or is partial, the process halts for manual reconciliation instead of automatically retrying. It cannot guarantee a hedge. Use conservative sizes and monitor inventory.

The zUSDT proceeds/cost are compared 1:1 with USDT for the estimate. A zUSDT depeg, redemption limitation, or liquidity discount is not currently modeled.

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

Fill in the RPC endpoint, KuCoin credentials, and wallet address. The wallet private key is only required for LIVE mode. Never commit `.env`. The wallet must already hold the relevant assets and have sufficient token allowances granted to the configured router; the bot does not issue approvals automatically. Router-based quotes can fail when the wallet has insufficient balance or allowance.

## Run

```bash
python main.py
```

Set `TRADING_MODE=DRY_RUN` for safe observation. The bot refuses LIVE mode unless the explicit confirmation variable is also set.

## Configuration notes

- The configured PlunderSwap V3 fee is expressed in V3 fee units (1,000,000 = 100%), not basis points. The default `100` corresponds to 0.01%; startup verifies it against the configured pool.
- WZIL uses 18 decimals and zUSDT uses 6 decimals in the current configuration; verify on-chain.
- The default per-size minimum-profit thresholds preserve the previous V3 strategy: 0.08, 0.18, 0.40 and 1.00 USDT for 5k, 10k, 15k and 25k ZIL respectively. The strategy prioritizes the largest size that passes all checks.
- The KuCoin market order uses ZIL quantity. The implementation checks final order status and filled size rather than treating order acceptance as a completed fill.
- Estimated network gas is converted to USDT using the current KuCoin ZIL bid. Gas cost uses the configured gas limit and multiplier, so treat it as a conservative estimate, not a guarantee of realized gas expenditure.
- Market profitability uses best bid/ask and does not yet model full order-book depth or guarantee execution at those prices.

## Tests

```bash
python -m pytest -q
```

Tests cover pure arbitrage arithmetic and safety-mode behavior. They do not replace fork testing or small-value live validation.
