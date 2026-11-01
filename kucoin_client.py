from __future__ import annotations

from decimal import Decimal

import aiohttp


class KuCoinClient:
    """Public, read-only KuCoin market-data client. No private API or order methods."""

    def __init__(self, base_url: str = "https://api.kucoin.com"):
        self.base_url = base_url.rstrip("/")
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.session and not self.session.closed:
            await self.session.close()

    async def ticker(self, symbol: str) -> tuple[Decimal, Decimal]:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        url = f"{self.base_url}/api/v1/market/orderbook/level1"
        async with self.session.get(url, params={"symbol": symbol}) as response:
            response.raise_for_status()
            payload = await response.json()
        if payload.get("code") != "200000" or not payload.get("data"):
            raise RuntimeError(f"KuCoin ticker error for {symbol}: {payload}")
        data = payload["data"]
        bid, ask = Decimal(data["bestBid"]), Decimal(data["bestAsk"])
        if bid <= 0 or ask <= 0 or bid > ask:
            raise ValueError(f"Invalid KuCoin top-of-book for {symbol}")
        return bid, ask

    async def order_book(self, symbol: str, depth: int = 100) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
        """Fetch public L2 depth; returned levels are (price, base-size)."""
        if depth not in {20, 100}:
            raise ValueError("KuCoin public L2 depth must be 20 or 100")
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        endpoint = f"/api/v3/market/orderbook/level2_{depth}"
        async with self.session.get(f"{self.base_url}{endpoint}", params={"symbol": symbol}) as response:
            response.raise_for_status()
            payload = await response.json()
        if payload.get("code") != "200000" or not payload.get("data"):
            raise RuntimeError(f"KuCoin order book error for {symbol}: {payload}")
        data = payload["data"]
        bids = [(Decimal(level[0]), Decimal(level[1])) for level in data.get("bids", [])]
        asks = [(Decimal(level[0]), Decimal(level[1])) for level in data.get("asks", [])]
        if not bids or not asks or any(price <= 0 or size < 0 for price, size in bids + asks):
            raise ValueError(f"Invalid or empty KuCoin order book for {symbol}")
        bids.sort(key=lambda level: level[0], reverse=True)
        asks.sort(key=lambda level: level[0])
        if bids[0][0] > asks[0][0]:
            raise ValueError(f"Crossed KuCoin order book for {symbol}")
        return bids, asks
