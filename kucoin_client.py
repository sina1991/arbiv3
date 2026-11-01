from __future__ import annotations

import time
from decimal import Decimal

import aiohttp

from models import OrderBook


class KuCoinPublicClient:
    """Public market-data client only. No authenticated endpoints or order methods."""

    def __init__(self, base_url: str = "https://api.kucoin.com"):
        self.base_url = base_url.rstrip("/")

    async def orderbook(self, symbol: str) -> OrderBook:
        url = f"{self.base_url}/api/v3/market/orderbook/level2_20"
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params={"symbol": symbol}) as response:
                response.raise_for_status()
                payload = await response.json()
        if payload.get("code") != "200000" or not payload.get("data"):
            raise RuntimeError(f"KuCoin order-book error for {symbol}: {payload}")
        data = payload["data"]
        bids = tuple((Decimal(p), Decimal(q)) for p, q in data.get("bids", []))
        asks = tuple((Decimal(p), Decimal(q)) for p, q in data.get("asks", []))
        if not bids or not asks:
            raise ValueError(f"Empty KuCoin order book for {symbol}")
        return OrderBook(bids=bids, asks=asks, observed_at=time.time())
