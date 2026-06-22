"""
STON.fi Omniston WebSocket client.

Handles quote requests and transaction building via the Omniston
liquidity-aggregation protocol. Every quote embeds the referrer address
so that 0.3 % of each swap accrues to the bot owner automatically on-chain.

Protocol reference: https://docs.ston.fi/developer-section/omniston
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OMNISTON_WS_URL: str = "wss://omni-ws.ston.fi"
OMNISTON_WS_SANDBOX_URL: str = "wss://omni-ws-sandbox.ston.fi"

# TON blockchain identifier used by Omniston
BLOCKCHAIN_TON: int = 607

# Referral fee in basis points (1 bps = 0.01 %).  30 bps = 0.30 %.
# DEX v1 hard-caps at 10 bps; flexible_referrer_fee lets the protocol
# fall back to 10 bps on v1 routes while keeping 30 bps on v2/DeDust/Tonco.
DEFAULT_REFERRER_FEE_BPS: int = 30

# Maximum slippage in basis points (100 bps = 1 %)
DEFAULT_SLIPPAGE_BPS: int = 100

# Seconds to wait for the first quote before giving up
QUOTE_TIMEOUT_SECONDS: float = 15.0

# Well-known TON token addresses
TOKENS: dict[str, str] = {
    "TON": "EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c",
    "USDT": "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs",
    "STON": "EQA2kCVNwVsil2EM2mB0SkXytxCqQjS4mttjDpnXmwG9T6bO",
    "NOT": "EQAvlWFDxGF2lXm67y4yzC17wYKD9A0guwPkMs1gOsM__NOT",
    "USDC": "EQB-MPwrd1G6WKNkLz_VnV6WqBDd142KMQv-g1O-8QUA3728",
}

# Decimals per token (used for human-readable ↔ on-chain unit conversion)
TOKEN_DECIMALS: dict[str, int] = {
    "TON": 9,
    "USDT": 6,
    "STON": 9,
    "NOT": 9,
    "USDC": 6,
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class SwapQuote:
    """A quote received from Omniston."""

    raw: dict[str, Any]
    from_token: str
    to_token: str
    from_amount_human: Decimal
    to_amount_human: Decimal
    referrer_fee_bps: int
    slippage_bps: int

    @property
    def rate(self) -> Decimal:
        """How many to_token units are received per 1 from_token unit."""
        if self.from_amount_human == 0:
            return Decimal("0")
        return self.to_amount_human / self.from_amount_human

    @property
    def fee_amount_human(self) -> Decimal:
        """Estimated referral fee in from_token units."""
        return (self.from_amount_human * self.referrer_fee_bps / Decimal("10000")).quantize(
            Decimal("0.000001")
        )

    def summary(self) -> str:
        """Human-readable one-liner for Telegram messages."""
        return (
            f"Свап: {self.from_amount_human} {self.from_token} → "
            f"{self.to_amount_human:.6f} {self.to_token}\n"
            f"Курс: 1 {self.from_token} = {self.rate:.4f} {self.to_token}\n"
            f"Наша комиссия: {self.fee_amount_human} {self.from_token} "
            f"({self.referrer_fee_bps / 100:.2f}%)"
        )


@dataclass
class BuildTransferResult:
    """Result from Omniston's transaction builder."""

    messages: list[dict[str, Any]]
    raw: dict[str, Any]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def token_address(symbol: str) -> str:
    """Return on-chain address for a token symbol (upper-case)."""
    sym = symbol.upper()
    if sym not in TOKENS:
        raise ValueError(f"Неизвестный токен: {symbol}. Доступные: {', '.join(TOKENS)}")
    return TOKENS[sym]


def to_units(amount: Decimal, symbol: str) -> str:
    """Convert human amount (e.g. 1.5 TON) to on-chain integer string."""
    decimals = TOKEN_DECIMALS.get(symbol.upper(), 9)
    units = int(amount * Decimal(10**decimals))
    return str(units)


def from_units(units: int | str, symbol: str) -> Decimal:
    """Convert on-chain integer units to human-readable Decimal."""
    decimals = TOKEN_DECIMALS.get(symbol.upper(), 9)
    return Decimal(str(units)) / Decimal(10**decimals)


def _rpc_message(method: str, params: dict[str, Any]) -> str:
    """Serialize a JSON-RPC-style message for Omniston WebSocket."""
    return json.dumps({"method": method, "id": str(uuid.uuid4()), "params": params})


# ---------------------------------------------------------------------------
# Core async functions
# ---------------------------------------------------------------------------


async def request_quote(
    *,
    from_token: str,
    to_token: str,
    from_amount: Decimal,
    wallet_address: str,
    referrer_address: str,
    referrer_fee_bps: int = DEFAULT_REFERRER_FEE_BPS,
    slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
    sandbox: bool = False,
) -> SwapQuote:
    """Request a swap quote from Omniston with our referral fee embedded.

    Args:
        from_token: Symbol of the token to sell (e.g. "TON").
        to_token: Symbol of the token to buy (e.g. "USDT").
        from_amount: Amount to sell in human units (e.g. Decimal("1.5")).
        wallet_address: Raw hex address of the user's TON wallet.
        referrer_address: Our wallet address — referral fees land here.
        referrer_fee_bps: Fee in basis points (default 30 = 0.30 %).
        slippage_bps: Max acceptable slippage (default 100 = 1 %).
        sandbox: Use sandbox endpoint for testing.

    Returns:
        SwapQuote with the best available route and embedded referral.

    Raises:
        TimeoutError: If no quote arrives within QUOTE_TIMEOUT_SECONDS.
        ValueError: If the token symbols are unknown.
    """
    ws_url = OMNISTON_WS_SANDBOX_URL if sandbox else OMNISTON_WS_URL

    bid_units = to_units(from_amount, from_token)

    params: dict[str, Any] = {
        "bid_asset_address": {
            "blockchain": BLOCKCHAIN_TON,
            "address": token_address(from_token),
        },
        "ask_asset_address": {
            "blockchain": BLOCKCHAIN_TON,
            "address": token_address(to_token),
        },
        "amount": {"bid_units": bid_units},
        "referrer_address": {
            "blockchain": BLOCKCHAIN_TON,
            "address": referrer_address,
        },
        "referrer_fee_bps": referrer_fee_bps,
        "settlement_methods": [0],  # 0 = swap (on-chain)
        "settlement_params": {
            "max_price_slippage_bps": slippage_bps,
            "gasless_settlement": 0,
            "flexible_referrer_fee": True,
            "wallet_address": {
                "blockchain": BLOCKCHAIN_TON,
                "address": wallet_address,
            },
        },
    }

    message = _rpc_message("v1beta8.quote", params)

    log.info(
        "Запрашиваю котировку: %s %s → %s (реферал %s bps)",
        from_amount,
        from_token,
        to_token,
        referrer_fee_bps,
    )

    async with websockets.connect(ws_url) as ws:  # type: ignore[attr-defined]
        await ws.send(message)
        quote_raw = await _wait_for_quote(ws)

    # Parse the ask amount from the response
    ask_units_raw = (
        quote_raw.get("result", {})
        .get("quote", {})
        .get("ask_units")
        or quote_raw.get("ask_units")
        or "0"
    )
    to_amount = from_units(int(ask_units_raw), to_token)

    return SwapQuote(
        raw=quote_raw,
        from_token=from_token.upper(),
        to_token=to_token.upper(),
        from_amount_human=from_amount,
        to_amount_human=to_amount,
        referrer_fee_bps=referrer_fee_bps,
        slippage_bps=slippage_bps,
    )


async def _wait_for_quote(ws: ClientConnection) -> dict[str, Any]:
    """Read WebSocket messages until we get a usable quote or time out."""
    deadline = asyncio.get_event_loop().time() + QUOTE_TIMEOUT_SECONDS
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError("Котировка не получена за отведённое время.")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        data: dict[str, Any] = json.loads(raw)

        # Omniston streams multiple events; we want the first real quote
        method = data.get("method", "")
        if "quoteUpdated" in method or "quote_updated" in method:
            return data
        # Also accept direct result responses
        if "result" in data and "quote" in data.get("result", {}):
            return data
        log.debug("Пропускаю сообщение: %s", method)


async def build_transfer(
    quote: SwapQuote,
    wallet_address: str,
    sandbox: bool = False,
) -> BuildTransferResult:
    """Ask Omniston to build the on-chain transfer messages for a quote.

    Args:
        quote: A SwapQuote previously obtained from request_quote().
        wallet_address: Raw hex address of the sender's wallet.
        sandbox: Use sandbox endpoint.

    Returns:
        BuildTransferResult containing a list of messages to sign and send.
    """
    ws_url = OMNISTON_WS_SANDBOX_URL if sandbox else OMNISTON_WS_URL

    params: dict[str, Any] = {
        "quote": quote.raw,
        "sender_address": {
            "blockchain": BLOCKCHAIN_TON,
            "address": wallet_address,
        },
    }

    message = _rpc_message("v1beta7.transaction.build_transfer", params)

    log.info("Строю транзакцию для свапа %s → %s", quote.from_token, quote.to_token)

    async with websockets.connect(ws_url) as ws:  # type: ignore[attr-defined]
        await ws.send(message)
        deadline = asyncio.get_event_loop().time() + QUOTE_TIMEOUT_SECONDS
        remaining = deadline - asyncio.get_event_loop().time()
        raw_response = await asyncio.wait_for(ws.recv(), timeout=remaining)

    result: dict[str, Any] = json.loads(raw_response)
    messages: list[dict[str, Any]] = (
        result.get("result", {}).get("messages")
        or result.get("messages")
        or []
    )

    return BuildTransferResult(messages=messages, raw=result)
