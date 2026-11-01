from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in os.getenv(name, default).split(",") if part.strip())


@dataclass(frozen=True)
class ScannerSettings:
    networks: tuple[str, ...] = _csv(
        "SCANNER_NETWORKS", "zilliqa,eth,bsc,polygon_pos,arbitrum,optimism,base"
    )
    trade_sizes_usdt: tuple[Decimal, ...] = tuple(
        Decimal(value) for value in _csv("TRADE_SIZES_USDT", "100,250,500,1000,2500,5000")
    )
    quote_symbols: tuple[str, ...] = tuple(
        value.upper() for value in _csv("SCANNER_QUOTE_SYMBOLS", "USDT,USDC,DAI,WETH,WBNB")
    )
    min_pool_liquidity_usd: Decimal = Decimal(os.getenv("MIN_POOL_LIQUIDITY_USD", "10000"))
    discovery_limit_per_network: int = int(os.getenv("DISCOVERY_LIMIT_PER_NETWORK", "20"))
    scan_interval_seconds: int = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))
    geckoterminal_base_url: str = os.getenv(
        "GECKOTERMINAL_BASE_URL", "https://api.geckoterminal.com/api/v2"
    )
    report_path: str = os.getenv("SCANNER_REPORT_PATH", "reports/latest-markets.json")

    def validate(self) -> None:
        if not self.networks:
            raise ValueError("SCANNER_NETWORKS must contain at least one network")
        if not self.trade_sizes_usdt or any(size <= 0 for size in self.trade_sizes_usdt):
            raise ValueError("TRADE_SIZES_USDT must contain positive values")
        if tuple(sorted(set(self.trade_sizes_usdt))) != self.trade_sizes_usdt:
            raise ValueError("TRADE_SIZES_USDT must be unique and ascending")
        if self.min_pool_liquidity_usd < 0:
            raise ValueError("MIN_POOL_LIQUIDITY_USD cannot be negative")
        if self.discovery_limit_per_network < 1 or self.scan_interval_seconds < 1:
            raise ValueError("Discovery limit and scan interval must be positive")


settings = ScannerSettings()
