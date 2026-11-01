from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv()


def _decimal(name: str, default: str) -> Decimal:
    return Decimal(os.getenv(name, default))


def _int(name: str, default: str) -> int:
    return int(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    trading_mode: str = os.getenv("TRADING_MODE", "DRY_RUN").upper()
    live_confirmation: str = os.getenv("LIVE_TRADING_CONFIRM", "")
    rpc_url: str = os.getenv("ZILLIQA_RPC_URL", "https://public.moonlet.cloud/zilliqa")
    chain_id: int = _int("CHAIN_ID", "32769")

    router: str = os.getenv("PLUNDERSWAP_V3_ROUTER", "0xe30161F32A019d876F082d9FF13ed451a03A2086")
    pool: str = os.getenv("PLUNDERSWAP_V3_POOL", "0xD9969Fac78afC7C696443802C0b8cFf1b1B45Fc8")
    wzil: str = os.getenv("WZIL_ADDRESS", "0x94e18aE7dd5eE57B55f30c4B63E2760c09EFb192")
    zusdt: str = os.getenv("ZUSDT_ADDRESS", "0x2274005778063684fbB1BfA96a2b725dc37d75f9")
    v3_fee: int = _int("PLUNDERSWAP_V3_FEE", "100")
    wzil_decimals: int = _int("WZIL_DECIMALS", "18")
    zusdt_decimals: int = _int("ZUSDT_DECIMALS", "6")

    wallet_address: str = os.getenv("WALLET_ADDRESS", "")
    wallet_private_key: str = os.getenv("WALLET_PRIVATE_KEY", "")

    kucoin_base_url: str = os.getenv("KUCOIN_BASE_URL", "https://api.kucoin.com")
    kucoin_key: str = os.getenv("KUCOIN_API_KEY", "")
    kucoin_secret: str = os.getenv("KUCOIN_API_SECRET", "")
    kucoin_passphrase: str = os.getenv("KUCOIN_API_PASSPHRASE", "")
    kucoin_key_version: str = os.getenv("KUCOIN_API_KEY_VERSION", "2")
    kucoin_symbol: str = os.getenv("KUCOIN_SYMBOL", "ZIL-USDT")
    kucoin_fee_rate: Decimal = _decimal("KUCOIN_FEE_RATE", "0.001")

    trade_sizes_zil: tuple[Decimal, ...] = tuple(
        Decimal(x.strip()) for x in os.getenv("TRADE_SIZES_ZIL", "5000,10000,15000,25000").split(",") if x.strip()
    )
    min_profit_by_size: tuple[Decimal, ...] = tuple(
        Decimal(x.strip()) for x in os.getenv("MIN_PROFIT_USDT_BY_SIZE", "0.08,0.18,0.40,1.0").split(",") if x.strip()
    )
    slippage_bps: int = _int("SLIPPAGE_BPS", "30")
    check_interval: int = _int("CHECK_INTERVAL_SECONDS", "6")
    max_price_age: int = _int("MAX_PRICE_AGE_SECONDS", "5")
    gas_limit: int = _int("GAS_LIMIT", "450000")
    gas_multiplier: Decimal = _decimal("GAS_MULTIPLIER", "2.0")

    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    def validate(self) -> None:
        if self.trading_mode not in {"DRY_RUN", "LIVE"}:
            raise ValueError("TRADING_MODE must be DRY_RUN or LIVE")
        if not self.wallet_address:
            raise RuntimeError("WALLET_ADDRESS is required for router-based V3 quotes")
        if self.trading_mode == "LIVE":
            if self.live_confirmation != "I_UNDERSTAND_REAL_ORDERS":
                raise RuntimeError("LIVE mode blocked: set LIVE_TRADING_CONFIRM=I_UNDERSTAND_REAL_ORDERS")
            if not self.wallet_private_key:
                raise RuntimeError("LIVE mode requires WALLET_PRIVATE_KEY")
            if not all((self.kucoin_key, self.kucoin_secret, self.kucoin_passphrase)):
                raise RuntimeError("LIVE mode requires KuCoin API credentials")
        if not (0 < self.v3_fee < 1_000_000):
            raise ValueError("PLUNDERSWAP_V3_FEE must be a valid V3 fee value")
        if not (0 <= self.slippage_bps < 10_000):
            raise ValueError("SLIPPAGE_BPS must be between 0 and 9999")
        if self.gas_limit <= 0 or self.gas_multiplier <= 0:
            raise ValueError("Gas limit and multiplier must be positive")
        if not self.trade_sizes_zil or any(size <= 0 for size in self.trade_sizes_zil):
            raise ValueError("TRADE_SIZES_ZIL must contain positive amounts")
        if len(self.min_profit_by_size) != len(self.trade_sizes_zil):
            raise ValueError("MIN_PROFIT_USDT_BY_SIZE must contain one threshold per trade size")
        if any(value < 0 for value in self.min_profit_by_size):
            raise ValueError("Profit thresholds cannot be negative")

    def profit_threshold(self, size: Decimal) -> Decimal:
        return dict(zip(self.trade_sizes_zil, self.min_profit_by_size))[size]


settings = Settings()
