from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class Direction(str, Enum):
    BUY_WZIL_ON_DEX_SELL_ZIL_ON_CEX = "BUY_WZIL_ON_DEX_SELL_ZIL_ON_CEX"
    BUY_ZIL_ON_CEX_SELL_WZIL_ON_DEX = "BUY_ZIL_ON_CEX_SELL_WZIL_ON_DEX"


@dataclass(frozen=True)
class V3Quote:
    amount_in: int
    amount_out: int
    token_in: str
    token_out: str


@dataclass(frozen=True)
class Opportunity:
    direction: Direction
    zil_size: Decimal
    dex_input_usdt: Decimal
    dex_output_usdt: Decimal
    kucoin_buy_cost_usdt: Decimal
    kucoin_sell_proceeds_usdt: Decimal
    estimated_profit_usdt: Decimal
    gas_cost_usdt: Decimal | None
    executable: bool
    reason: str
