from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from web3 import Web3

from config import Settings

ROUTER_ABI = [
    {
        "inputs": [{
            "components": [
                {"internalType": "address", "name": "tokenIn", "type": "address"},
                {"internalType": "address", "name": "tokenOut", "type": "address"},
                {"internalType": "uint24", "name": "fee", "type": "uint24"},
                {"internalType": "address", "name": "recipient", "type": "address"},
                {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
            ],
            "internalType": "struct IV3SwapRouter.ExactInputSingleParams",
            "name": "params", "type": "tuple"
        }],
        "name": "exactInputSingle", "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable", "type": "function"
    },
    {
        "inputs": [{
            "components": [
                {"internalType": "address", "name": "tokenIn", "type": "address"},
                {"internalType": "address", "name": "tokenOut", "type": "address"},
                {"internalType": "uint24", "name": "fee", "type": "uint24"},
                {"internalType": "address", "name": "recipient", "type": "address"},
                {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
                {"internalType": "uint256", "name": "amountInMaximum", "type": "uint256"},
                {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
            ],
            "internalType": "struct IV3SwapRouter.ExactOutputSingleParams",
            "name": "params", "type": "tuple"
        }],
        "name": "exactOutputSingle", "outputs": [{"internalType": "uint256", "name": "amountIn", "type": "uint256"}],
        "stateMutability": "payable", "type": "function"
    },
]
ERC20_ABI = [
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
]
POOL_ABI = [
    {"inputs":[],"name":"token0","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"token1","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"fee","outputs":[{"type":"uint24"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"slot0","outputs":[
        {"type":"uint160"},{"type":"int24"},{"type":"uint16"},{"type":"uint16"},
        {"type":"uint16"},{"type":"uint32"},{"type":"bool"}
    ],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"liquidity","outputs":[{"type":"uint128"}],"stateMutability":"view","type":"function"},
]


def to_raw(amount: Decimal, decimals: int) -> int:
    return int(amount * (Decimal(10) ** decimals))


def from_raw(amount: int, decimals: int) -> Decimal:
    return Decimal(amount) / (Decimal(10) ** decimals)


class PendingTransactionError(RuntimeError):
    """A transaction was broadcast but its final receipt could not be confirmed."""


class PlunderSwapV3:
    """Direct interaction with the configured V3 pool and router only."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.w3 = Web3(Web3.HTTPProvider(settings.rpc_url, request_kwargs={"timeout": 15}))
        if not self.w3.is_connected():
            raise ConnectionError("Cannot connect to Zilliqa EVM RPC")
        actual_chain_id = int(self.w3.eth.chain_id)
        if actual_chain_id != settings.chain_id:
            raise RuntimeError(f"Wrong chain: configured {settings.chain_id}, RPC reports {actual_chain_id}")
        self.wallet = self.w3.to_checksum_address(settings.wallet_address)
        self.router_address = self.w3.to_checksum_address(settings.router)
        self.wzil_address = self.w3.to_checksum_address(settings.wzil)
        self.zusdt_address = self.w3.to_checksum_address(settings.zusdt)
        self.router = self.w3.eth.contract(address=self.router_address, abi=ROUTER_ABI)
        self.pool = self.w3.eth.contract(
            address=self.w3.to_checksum_address(settings.pool), abi=POOL_ABI
        )
        self.wzil = self.w3.eth.contract(address=self.wzil_address, abi=ERC20_ABI)
        self.zusdt = self.w3.eth.contract(address=self.zusdt_address, abi=ERC20_ABI)
        self._validate_pool()

    def _validate_pool(self) -> None:
        token0 = self.pool.functions.token0().call()
        token1 = self.pool.functions.token1().call()
        actual = {token0.lower(), token1.lower()}
        expected = {self.wzil_address.lower(), self.zusdt_address.lower()}
        if actual != expected:
            raise ValueError(f"Configured V3 pool token mismatch: {token0}, {token1}")
        actual_fee = self.pool.functions.fee().call()
        if int(actual_fee) != self.settings.v3_fee:
            raise ValueError(
                f"Configured V3 fee {self.settings.v3_fee} differs from pool fee {actual_fee}"
            )
        slot0 = self.pool.functions.slot0().call()
        liquidity = self.pool.functions.liquidity().call()
        if not slot0[0] or not liquidity:
            raise ValueError("V3 pool has no valid active price/liquidity")

    def balances(self) -> dict[str, Decimal]:
        return {
            "wzil": from_raw(self.wzil.functions.balanceOf(self.wallet).call(), self.settings.wzil_decimals),
            "zusdt": from_raw(self.zusdt.functions.balanceOf(self.wallet).call(), self.settings.zusdt_decimals),
            "native_zil": from_raw(self.w3.eth.get_balance(self.wallet), 18),
        }

    def allowances(self) -> dict[str, int]:
        return {
            "wzil": self.wzil.functions.allowance(self.wallet, self.router_address).call(),
            "zusdt": self.zusdt.functions.allowance(self.wallet, self.router_address).call(),
        }

    def quote_sell_wzil(self, zil_amount: Decimal) -> tuple[int, int]:
        """Exact-input WZIL -> zUSDT quote, evaluated by the deployed router."""
        amount_in = to_raw(zil_amount, self.settings.wzil_decimals)
        params = (
            self.wzil_address, self.zusdt_address, self.settings.v3_fee,
            self.wallet, amount_in, 0, 0
        )
        amount_out = self.router.functions.exactInputSingle(params).call({"from": self.wallet})
        return amount_in, int(amount_out)

    def quote_buy_wzil(self, zil_amount: Decimal) -> tuple[int, int]:
        """Exact-output zUSDT -> WZIL quote, evaluated by the deployed router."""
        amount_out = to_raw(zil_amount, self.settings.wzil_decimals)
        params = (
            self.zusdt_address, self.wzil_address, self.settings.v3_fee,
            self.wallet, amount_out, 2**256 - 1, 0
        )
        amount_in = self.router.functions.exactOutputSingle(params).call({"from": self.wallet})
        return int(amount_in), amount_out

    def build_swap_transaction(
        self, direction: str, amount_in: int, amount_out: int
    ) -> dict[str, Any]:
        gas_price = int(Decimal(self.w3.eth.gas_price) * self.settings.gas_multiplier)
        nonce = self.w3.eth.get_transaction_count(self.wallet, "pending")
        slippage = Decimal(self.settings.slippage_bps) / Decimal(10_000)
        if direction == "SELL_WZIL":
            params = (
                self.wzil_address, self.zusdt_address, self.settings.v3_fee,
                self.wallet, amount_in, int(Decimal(amount_out) * (1 - slippage)), 0
            )
            call = self.router.functions.exactInputSingle(params)
        elif direction == "BUY_WZIL":
            params = (
                self.zusdt_address, self.wzil_address, self.settings.v3_fee,
                self.wallet, amount_out, int(Decimal(amount_in) * (1 + slippage)), 0
            )
            call = self.router.functions.exactOutputSingle(params)
        else:
            raise ValueError(f"Unsupported V3 swap direction: {direction}")
        return call.build_transaction({
            "from": self.wallet,
            "chainId": self.settings.chain_id,
            "nonce": nonce,
            "gas": self.settings.gas_limit,
            "gasPrice": gas_price,
            "value": 0,
        })

    def execute_swap(self, direction: str, amount_in: int, amount_out: int) -> str:
        if self.settings.trading_mode != "LIVE":
            raise RuntimeError("Transaction submission blocked outside LIVE mode")
        if not self.settings.wallet_private_key:
            raise RuntimeError("Missing wallet private key")
        derived_address = self.w3.eth.account.from_key(self.settings.wallet_private_key).address
        if derived_address.lower() != self.wallet.lower():
            raise RuntimeError("WALLET_PRIVATE_KEY does not match WALLET_ADDRESS")
        txn = self.build_swap_transaction(direction, amount_in, amount_out)
        signed = self.w3.eth.account.sign_transaction(txn, self.settings.wallet_private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        try:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
        except Exception as exc:
            raise PendingTransactionError(
                f"Transaction was broadcast but receipt is unconfirmed: {tx_hash.hex()}. "
                "Stop and reconcile this hash before restarting."
            ) from exc
        if receipt.status != 1:
            raise RuntimeError(f"PlunderSwap V3 transaction reverted: {tx_hash.hex()}")
        return tx_hash.hex()

    def gas_cost_usdt(self, zil_usdt_price: Decimal) -> Decimal:
        gas_price = Decimal(self.w3.eth.gas_price) * self.settings.gas_multiplier
        gas_zil = Decimal(self.settings.gas_limit) * gas_price / (Decimal(10) ** 18)
        return gas_zil * zil_usdt_price
