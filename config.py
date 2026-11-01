from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Read-only application settings; intentionally contains no credentials or signing keys."""

    registry_path: str = os.getenv("MARKET_REGISTRY_PATH", "market_registry.json")
    check_interval: int = int(os.getenv("CHECK_INTERVAL_SECONDS", "15"))
    kucoin_base_url: str = os.getenv("KUCOIN_BASE_URL", "https://api.kucoin.com")

    def validate(self) -> None:
        if self.check_interval < 1:
            raise ValueError("CHECK_INTERVAL_SECONDS must be positive")


settings = Settings()
