from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal
from urllib.parse import urlencode

import aiohttp

from config import Settings


class KuCoinClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.session and not self.session.closed:
            await self.session.close()

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

    async def ticker(self) -> tuple[Decimal, Decimal]:
        await self._ensure_session()
        url = f"{self.settings.kucoin_base_url}/api/v1/market/orderbook/level1"
        async with self.session.get(url, params={"symbol": self.settings.kucoin_symbol}) as response:
            response.raise_for_status()
            payload = await response.json()
        if payload.get("code") != "200000" or not payload.get("data"):
            raise RuntimeError(f"KuCoin ticker error: {payload}")
        data = payload["data"]
        bid, ask = Decimal(data["bestBid"]), Decimal(data["bestAsk"])
        if bid <= 0 or ask <= 0 or bid > ask:
            raise ValueError("Invalid KuCoin top-of-book")
        return bid, ask

    def _headers(self, method: str, endpoint: str, body: str = "") -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        secret = self.settings.kucoin_secret.encode()
        signature_payload = f"{timestamp}{method.upper()}{endpoint}{body}".encode()
        signature = base64.b64encode(hmac.new(secret, signature_payload, hashlib.sha256).digest()).decode()
        passphrase = base64.b64encode(
            hmac.new(secret, self.settings.kucoin_passphrase.encode(), hashlib.sha256).digest()
        ).decode()
        return {
            "KC-API-KEY": self.settings.kucoin_key,
            "KC-API-SIGN": signature,
            "KC-API-TIMESTAMP": timestamp,
            "KC-API-PASSPHRASE": passphrase,
            "KC-API-KEY-VERSION": self.settings.kucoin_key_version,
            "Content-Type": "application/json",
        }

    async def _private_request(self, method: str, path: str, body: dict | None = None):
        await self._ensure_session()
        body_text = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = self._headers(method, path, body_text)
        async with self.session.request(
            method, self.settings.kucoin_base_url + path, headers=headers,
            data=body_text if body is not None else None
        ) as response:
            payload = await response.json()
            if response.status >= 400 or payload.get("code") != "200000":
                raise RuntimeError(f"KuCoin private API error ({response.status}): {payload}")
            return payload.get("data")

    async def account_balances(self) -> dict[str, Decimal]:
        """Return available trade-account balances keyed by currency."""
        data = await self._private_request("GET", "/api/v1/accounts")
        balances: dict[str, Decimal] = {}
        for account in data or []:
            if account.get("type") != "trade":
                continue
            currency = str(account.get("currency", "")).upper()
            available = Decimal(str(account.get("available", "0")))
            balances[currency] = balances.get(currency, Decimal("0")) + available
        return balances

    async def market_order_and_verify(self, side: str, zil_size: Decimal) -> dict:
        if side not in {"buy", "sell"}:
            raise ValueError("KuCoin side must be buy or sell")
        if not all((self.settings.kucoin_key, self.settings.kucoin_secret, self.settings.kucoin_passphrase)):
            raise RuntimeError("KuCoin API credentials are not configured")
        body = {
            "clientOid": str(uuid.uuid4()),
            "side": side,
            "symbol": self.settings.kucoin_symbol,
            "type": "market",
            "size": format(zil_size.quantize(Decimal("0.0001")), "f"),
        }
        created = await self._private_request("POST", "/api/v1/orders", body)
        order_id = created.get("orderId")
        if not order_id:
            raise RuntimeError(f"KuCoin did not return orderId: {created}")

        # An accepted order is not a fill. Poll its final execution details.
        deadline = time.monotonic() + 30
        details = None
        while time.monotonic() < deadline:
            details = await self._private_request("GET", f"/api/v1/orders/{order_id}")
            deal_size = Decimal(str(details.get("dealSize", "0")))
            if deal_size >= zil_size.quantize(Decimal("0.0001")):
                break
            if details.get("isActive") is False:
                break
            await __import__("asyncio").sleep(1)

        if not details:
            raise RuntimeError(f"KuCoin order status unavailable: {order_id}")
        filled = Decimal(str(details.get("dealSize", "0")))
        funds = Decimal(str(details.get("dealFunds", "0")))
        if filled <= 0:
            raise RuntimeError(f"KuCoin order was not filled: {order_id}; details={details}")
        return {
            "order_id": order_id,
            "side": side,
            "requested_size": zil_size,
            "filled_size": filled,
            "deal_funds": funds,
            "average_price": funds / filled if filled else Decimal("0"),
            "fully_filled": filled >= zil_size.quantize(Decimal("0.0001")),
        }
