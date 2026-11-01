from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from web3 import Web3


class QuoteAdapter(Protocol):
    def quote_exact_input(self, token_in: str, token_out: str, amount_in: Decimal) -> Decimal:
        """Return output amount in human token units; read-only operation."""


@dataclass(frozen=True)
class V2PoolState:
    token0: str
    token1: str
    reserve0: int
    reserve1: int
    decimals0: int
    decimals1: int
    fee_bps: int

    def __post_init__(self):
        if not 0 <= self.fee_bps < 10_000:
            raise ValueError("fee_bps must be between 0 and 9999")
        if min(self.reserve0, self.reserve1) <= 0:
            raise ValueError("Pool reserves must be positive")
        if not 0 <= self.decimals0 <= 36 or not 0 <= self.decimals1 <= 36:
            raise ValueError("Token decimals are outside the supported range")

    def quote_exact_input(self, token_in: str, token_out: str, amount_in: Decimal) -> Decimal:
        if amount_in <= 0:
            raise ValueError("amount_in must be positive")
        if token_in.lower() == self.token0.lower() and token_out.lower() == self.token1.lower():
            reserve_in, reserve_out = self.reserve0, self.reserve1
            decimals_in, decimals_out = self.decimals0, self.decimals1
        elif token_in.lower() == self.token1.lower() and token_out.lower() == self.token0.lower():
            reserve_in, reserve_out = self.reserve1, self.reserve0
            decimals_in, decimals_out = self.decimals1, self.decimals0
        else:
            raise ValueError("Requested token direction does not match this pool")

        raw_in = int(amount_in * (Decimal(10) ** decimals_in))
        amount_with_fee = raw_in * (10_000 - self.fee_bps)
        raw_out = (amount_with_fee * reserve_out) // (
            reserve_in * 10_000 + amount_with_fee
        )
        return Decimal(raw_out) / (Decimal(10) ** decimals_out)


class UniswapV3QuoterV2:
    """Read-only Uniswap V3-compatible QuoterV2 adapter; address must be verified per deployment."""

    ABI = [
        {
            "inputs": [{"components": [
                {"name": "tokenIn", "type": "address"},
                {"name": "tokenOut", "type": "address"},
                {"name": "amountIn", "type": "uint256"},
                {"name": "fee", "type": "uint24"},
                {"name": "sqrtPriceLimitX96", "type": "uint160"},
            ], "name": "params", "type": "tuple"}],
            "name": "quoteExactInputSingle",
            "outputs": [
                {"name": "amountOut", "type": "uint256"},
                {"name": "sqrtPriceX96After", "type": "uint160"},
                {"name": "initializedTicksCrossed", "type": "uint32"},
                {"name": "gasEstimate", "type": "uint256"},
            ],
            "stateMutability": "nonpayable", "type": "function",
        },
        {
            "inputs": [{"components": [
                {"name": "tokenIn", "type": "address"},
                {"name": "tokenOut", "type": "address"},
                {"name": "amount", "type": "uint256"},
                {"name": "fee", "type": "uint24"},
                {"name": "sqrtPriceLimitX96", "type": "uint160"},
            ], "name": "params", "type": "tuple"}],
            "name": "quoteExactOutputSingle",
            "outputs": [
                {"name": "amountIn", "type": "uint256"},
                {"name": "sqrtPriceX96After", "type": "uint160"},
                {"name": "initializedTicksCrossed", "type": "uint32"},
                {"name": "gasEstimate", "type": "uint256"},
            ],
            "stateMutability": "nonpayable", "type": "function",
        },
    ]

    def __init__(
        self, rpc_url: str, quoter_address: str, fee_units: int,
        token_decimals: dict[str, int], timeout: int = 15,
    ):
        if not 0 < fee_units < 1_000_000:
            raise ValueError("V3 fee must be expressed in valid fee units")
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout}))
        if not self.w3.is_connected():
            raise ConnectionError("Cannot connect to configured chain RPC")
        self.address = self.w3.to_checksum_address(quoter_address)
        if not self.w3.eth.get_code(self.address):
            raise ValueError("Configured Quoter address has no contract code")
        self.contract = self.w3.eth.contract(address=self.address, abi=self.ABI)
        self.fee_units = fee_units
        self.decimals = {key.lower(): value for key, value in token_decimals.items()}

    def quote_exact_input(self, token_in: str, token_out: str, amount_in: Decimal) -> Decimal:
        token_in = self.w3.to_checksum_address(token_in)
        token_out = self.w3.to_checksum_address(token_out)
        decimals_in = self.decimals[token_in.lower()]
        decimals_out = self.decimals[token_out.lower()]
        raw_in = int(amount_in * (Decimal(10) ** decimals_in))
        result = self.contract.functions.quoteExactInputSingle(
            (token_in, token_out, raw_in, self.fee_units, 0)
        ).call()
        return Decimal(int(result[0])) / (Decimal(10) ** decimals_out)

    def quote_exact_output(self, token_in: str, token_out: str, amount_out: Decimal) -> Decimal:
        token_in = self.w3.to_checksum_address(token_in)
        token_out = self.w3.to_checksum_address(token_out)
        decimals_in = self.decimals[token_in.lower()]
        decimals_out = self.decimals[token_out.lower()]
        raw_out = int(amount_out * (Decimal(10) ** decimals_out))
        result = self.contract.functions.quoteExactOutputSingle(
            (token_in, token_out, raw_out, self.fee_units, 0)
        ).call()
        return Decimal(int(result[0])) / (Decimal(10) ** decimals_in)
