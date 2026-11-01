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
