"""
Configuration loader.

All settings come from environment variables (loaded from .env by python-dotenv).
See .env.example for descriptions of each variable.

Единственный обязательный параметр — TELEGRAM_BOT_TOKEN.
Все остальные настраиваются автоматически или имеют разумные значения по умолчанию:

  REFERRER_TON_ADDRESS — если не задан, используется адрес кошелька бота
                         (создаётся автоматически при первом запуске).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    # Telegram bot token from @BotFather — единственный обязательный параметр
    telegram_token: str

    # TON адрес для реферальных комиссий.
    # Если пусто, telegram_bot.py подставит адрес кошелька бота автоматически.
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
    """Load and validate configuration from environment variables.

    Единственный обязательный параметр — TELEGRAM_BOT_TOKEN.
    REFERRER_TON_ADDRESS необязателен: если не задан, будет использован
    адрес кошелька бота (подставляется позже в telegram_bot.main()).
    """
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")

    if not telegram_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN не задан.\n"
            "Создай бота за 30 секунд:\n"
            "  1. Открой Telegram → @BotFather\n"
            "  2. Отправь /newbot\n"
            "  3. Введи имя и username бота\n"
            "  4. Скопируй токен в .env: TELEGRAM_BOT_TOKEN=токен"
        )

    return Config(
        telegram_token=telegram_token,
        # Пустая строка — будет заменена адресом кошелька бота в main()
        referrer_address=os.getenv("REFERRER_TON_ADDRESS", ""),
        toncenter_api_key=os.getenv("TONCENTER_API_KEY") or None,
        referrer_fee_bps=int(os.getenv("REFERRER_FEE_BPS", "30")),
        slippage_bps=int(os.getenv("SLIPPAGE_BPS", "100")),
        use_sandbox=os.getenv("USE_SANDBOX", "false").lower() == "true",
    )
