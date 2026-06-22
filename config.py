"""
Configuration loader.

All settings come from environment variables (loaded from .env by python-dotenv).
See .env.example for descriptions of each variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    # Telegram bot token from @BotFather
    telegram_token: str

    # Our TON wallet address (hex) — referral fees land here automatically
    referrer_address: str

    # Toncenter API key (get via @toncenter on Telegram)
    toncenter_api_key: str | None

    # Referral fee in basis points (30 = 0.30 %)
    referrer_fee_bps: int

    # Max slippage in basis points (100 = 1 %)
    slippage_bps: int

    # Set to "true" to connect to sandbox/testnet instead of mainnet
    use_sandbox: bool


def load_config() -> Config:
    """Load and validate configuration from environment variables."""
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    referrer_address = os.getenv("REFERRER_TON_ADDRESS", "")

    if not telegram_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN не задан. "
            "Создай бота через @BotFather и укажи токен в .env"
        )
    if not referrer_address:
        raise RuntimeError(
            "REFERRER_TON_ADDRESS не задан. "
            "Укажи TON адрес кошелька для получения комиссий в .env"
        )

    return Config(
        telegram_token=telegram_token,
        referrer_address=referrer_address,
        toncenter_api_key=os.getenv("TONCENTER_API_KEY") or None,
        referrer_fee_bps=int(os.getenv("REFERRER_FEE_BPS", "30")),
        slippage_bps=int(os.getenv("SLIPPAGE_BPS", "100")),
        use_sandbox=os.getenv("USE_SANDBOX", "false").lower() == "true",
    )
