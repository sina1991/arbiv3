from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation

import aiohttp

from models import PoolCandidate, Token


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _included_index(payload: dict) -> dict[str, dict]:
    return {
        item.get("id", ""): item
        for item in payload.get("included", [])
        if isinstance(item, dict)
    }


def _related_token(pool: dict, relation: str, included: dict[str, dict], chain: str) -> Token | None:
    relation_data = (pool.get("relationships") or {}).get(relation, {}).get("data") or {}
    item = included.get(relation_data.get("id", ""))
    if not item:
        return None
    attrs = item.get("attributes") or {}
    address = attrs.get("address")
    symbol = attrs.get("symbol")
    if not address or not symbol:
        return None
    decimals = attrs.get("decimals")
    return Token(
        chain=chain,
        address=str(address),
        symbol=str(symbol).upper(),
        name=str(attrs.get("name") or ""),
        decimals=int(decimals) if decimals is not None else None,
    )


class GeckoTerminalDiscovery:
    """Discovers pool metadata only; it never treats spot prices as executable quotes."""

    def __init__(self, base_url: str, session: aiohttp.ClientSession):
        self.base_url = base_url.rstrip("/")
        self.session = session

    async def discover(self, network: str, limit: int = 20) -> list[PoolCandidate]:
        url = f"{self.base_url}/networks/{network}/pools"
        params = {"page": "1", "include": "base_token,quote_token,dex"}
        async with self.session.get(url, params=params) as response:
            response.raise_for_status()
            payload = await response.json()

        included = _included_index(payload)
        candidates: list[PoolCandidate] = []
        for pool in payload.get("data", [])[:limit]:
            attrs = pool.get("attributes") or {}
            base = _related_token(pool, "base_token", included, network)
            quote = _related_token(pool, "quote_token", included, network)
            if not base or not quote:
                continue
            dex_data = (pool.get("relationships") or {}).get("dex", {}).get("data") or {}
            dex = included.get(dex_data.get("id", ""), {})
            dex_attrs = dex.get("attributes") or {}
            address = attrs.get("address")
            if not address:
                continue
            candidates.append(PoolCandidate(
                chain=network,
                pool_address=str(address),
                dex_id=str(dex_data.get("id") or ""),
                dex_name=str(dex_attrs.get("name") or dex_data.get("id") or "unknown"),
                base_token=base,
                quote_token=quote,
                liquidity_usd=_decimal(attrs.get("reserve_in_usd")),
                volume_24h_usd=_decimal((attrs.get("volume_usd") or {}).get("h24")),
                price_usd=_decimal(attrs.get("base_token_price_usd")) or None,
                url=attrs.get("pool_created_at"),
            ))
        return candidates

    async def discover_many(self, networks: tuple[str, ...], limit: int) -> tuple[list[PoolCandidate], dict[str, str]]:
        async def one(network: str):
            try:
                return network, await self.discover(network, limit), None
            except Exception as exc:
                return network, [], str(exc)

        results = await asyncio.gather(*(one(network) for network in networks))
        pools: list[PoolCandidate] = []
        errors: dict[str, str] = {}
        for network, found, error in results:
            pools.extend(found)
            if error:
                errors[network] = error
        return pools, errors
