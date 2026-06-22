"""
Telegram bot — STON.fi swap aggregator with referral fee collection.

Every swap a user makes through this bot earns 0.3 % to the bot owner
automatically on-chain via the STON.fi Omniston referral program.

Commands:
    /start   — Welcome and quick-start guide
    /help    — List all commands
    /tokens  — Supported tokens
    /balance — Bot wallet balance
    /quote <amount> <FROM> <TO>   — Get a swap quote (no execution)
    /swap  <amount> <FROM> <TO>   — Execute a swap (uses bot wallet)
    /earnings — Explain how referral income works

Usage:
    TELEGRAM_BOT_TOKEN=xxx REFERRER_TON_ADDRESS=EQ... python telegram_bot.py
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from config import Config, load_config
from omniston import TOKENS, request_quote
from ton_wallet import (
    WalletData,
    ensure_wallet,
    fetch_balance,
    sign_and_send_messages,
)
from omniston import build_transfer

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared state (injected at startup)
# ---------------------------------------------------------------------------

_config: Config
_bot_wallet: WalletData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_swap_args(args: list[str]) -> tuple[Decimal, str, str]:
    """Parse (amount, FROM, TO) from command arguments.

    Raises:
        ValueError: if the arguments are invalid.
    """
    if len(args) < 3:
        raise ValueError("Нужно 3 аргумента: <сумма> <откуда> <куда>\nПример: /swap 1 TON USDT")
    try:
        amount = Decimal(args[0].replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"Неверная сумма: {args[0]}") from exc
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля.")
    from_token = args[1].upper()
    to_token = args[2].upper()
    if from_token not in TOKENS:
        raise ValueError(f"Неизвестный токен: {from_token}. /tokens — список доступных.")
    if to_token not in TOKENS:
        raise ValueError(f"Неизвестный токен: {to_token}. /tokens — список доступных.")
    if from_token == to_token:
        raise ValueError("Токены должны быть разными.")
    return amount, from_token, to_token


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message with quick-start instructions."""
    text = (
        "Привет! Я бот для обмена токенов на TON через STON.fi.\n\n"
        "Каждый свап через меня идёт с лучшим курсом от агрегатора STON.fi Omniston "
        "— и часть комиссии автоматически уходит владельцу бота.\n\n"
        "Быстрый старт:\n"
        "  /tokens — посмотреть доступные токены\n"
        "  /balance — баланс бота\n"
        "  /quote 1 TON USDT — узнать курс\n"
        "  /swap 1 TON USDT — обменять\n\n"
        "Полный список команд: /help"
    )
    await update.message.reply_text(text)  # type: ignore[union-attr]


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all available commands."""
    text = (
        "Команды:\n\n"
        "/quote <сумма> <ОТ> <К>  — посмотреть курс без исполнения\n"
        "  Пример: /quote 1 TON USDT\n\n"
        "/swap <сумма> <ОТ> <К>   — выполнить обмен (с кошелька бота)\n"
        "  Пример: /swap 1 TON USDT\n\n"
        "/tokens  — список поддерживаемых токенов\n"
        "/balance — баланс бота в TON\n"
        "/earnings — как работает заработок на комиссиях\n"
    )
    await update.message.reply_text(text)  # type: ignore[union-attr]


async def cmd_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List supported token symbols."""
    lines = [f"  {sym} — {addr[:12]}..." for sym, addr in TOKENS.items()]
    text = "Поддерживаемые токены:\n\n" + "\n".join(lines)
    await update.message.reply_text(text)  # type: ignore[union-attr]


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the bot wallet balance."""
    await update.message.reply_text("Проверяю баланс...")  # type: ignore[union-attr]
    balance = await fetch_balance(
        _bot_wallet.address_hex,
        api_key=_config.toncenter_api_key,
        testnet=_config.use_sandbox,
    )
    fee_pct = _config.referrer_fee_bps / 100
    text = (
        f"Кошелёк бота:\n"
        f"  Адрес: {_bot_wallet.address_bounceable}\n"
        f"  Баланс: {balance:.4f} TON\n\n"
        f"Реферальная комиссия: {fee_pct:.2f}% с каждого свапа\n"
        f"Комиссии поступают на адрес:\n  {_config.referrer_address}"
    )
    await update.message.reply_text(text)  # type: ignore[union-attr]


async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get a swap quote without executing it."""
    args: list[str] = context.args or []  # type: ignore[assignment]
    try:
        amount, from_token, to_token = _parse_swap_args(args)
    except ValueError as exc:
        await update.message.reply_text(f"Ошибка: {exc}")  # type: ignore[union-attr]
        return

    await update.message.reply_text(  # type: ignore[union-attr]
        f"Запрашиваю котировку {amount} {from_token} → {to_token}..."
    )

    try:
        quote = await request_quote(
            from_token=from_token,
            to_token=to_token,
            from_amount=amount,
            wallet_address=_bot_wallet.address_hex,
            referrer_address=_config.referrer_address,
            referrer_fee_bps=_config.referrer_fee_bps,
            slippage_bps=_config.slippage_bps,
            sandbox=_config.use_sandbox,
        )
    except TimeoutError:
        await update.message.reply_text(  # type: ignore[union-attr]
            "Не удалось получить котировку за 15 секунд. Попробуй снова."
        )
        return
    except ValueError as exc:
        await update.message.reply_text(f"Ошибка: {exc}")  # type: ignore[union-attr]
        return
    except Exception as exc:
        log.exception("Ошибка при запросе котировки")
        await update.message.reply_text(f"Ошибка соединения: {exc}")  # type: ignore[union-attr]
        return

    text = (
        f"Котировка:\n\n"
        f"{quote.summary()}\n\n"
        f"Чтобы выполнить: /swap {amount} {from_token} {to_token}"
    )
    await update.message.reply_text(text)  # type: ignore[union-attr]


async def cmd_swap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get a quote and execute the swap from the bot wallet."""
    args: list[str] = context.args or []  # type: ignore[assignment]
    try:
        amount, from_token, to_token = _parse_swap_args(args)
    except ValueError as exc:
        await update.message.reply_text(f"Ошибка: {exc}")  # type: ignore[union-attr]
        return

    await update.message.reply_text(  # type: ignore[union-attr]
        f"Запрашиваю лучший курс для {amount} {from_token} → {to_token}..."
    )

    # Step 1: Get quote
    try:
        quote = await request_quote(
            from_token=from_token,
            to_token=to_token,
            from_amount=amount,
            wallet_address=_bot_wallet.address_hex,
            referrer_address=_config.referrer_address,
            referrer_fee_bps=_config.referrer_fee_bps,
            slippage_bps=_config.slippage_bps,
            sandbox=_config.use_sandbox,
        )
    except TimeoutError:
        await update.message.reply_text(  # type: ignore[union-attr]
            "Не удалось получить котировку. Попробуй снова."
        )
        return
    except Exception as exc:
        log.exception("Ошибка при запросе котировки")
        await update.message.reply_text(f"Ошибка: {exc}")  # type: ignore[union-attr]
        return

    await update.message.reply_text(  # type: ignore[union-attr]
        f"Курс найден:\n\n{quote.summary()}\n\nСтрою транзакцию..."
    )

    # Step 2: Build transfer
    try:
        transfer = await build_transfer(
            quote=quote,
            wallet_address=_bot_wallet.address_hex,
            sandbox=_config.use_sandbox,
        )
    except Exception as exc:
        log.exception("Ошибка при построении транзакции")
        await update.message.reply_text(f"Ошибка построения транзакции: {exc}")  # type: ignore[union-attr]
        return

    if not transfer.messages:
        await update.message.reply_text(  # type: ignore[union-attr]
            "Omniston не вернул транзакцию. Попробуй снова или измени сумму."
        )
        return

    await update.message.reply_text("Отправляю транзакцию в сеть TON...")  # type: ignore[union-attr]

    # Step 3: Sign and broadcast
    ok = await sign_and_send_messages(
        transfer.messages,
        _bot_wallet,
        api_key=_config.toncenter_api_key,
        testnet=_config.use_sandbox,
    )

    if ok:
        fee_pct = _config.referrer_fee_bps / 100
        fee_amount = quote.fee_amount_human
        await update.message.reply_text(  # type: ignore[union-attr]
            f"Свап выполнен!\n\n"
            f"{quote.summary()}\n\n"
            f"Реферальная комиссия {fee_pct:.2f}% ({fee_amount} {from_token}) "
            f"зачислена на кошелёк владельца бота автоматически."
        )
    else:
        await update.message.reply_text(  # type: ignore[union-attr]
            "Транзакция отправлена, но статус неизвестен. "
            "Проверь на https://tonscan.org"
        )


async def cmd_earnings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Explain how the referral income model works."""
    fee_pct = _config.referrer_fee_bps / 100
    text = (
        f"Как работает заработок:\n\n"
        f"1. Пользователь запрашивает свап через бота.\n"
        f"2. Бот отправляет запрос в STON.fi Omniston с нашим адресом "
        f"как реферером.\n"
        f"3. STON.fi автоматически зачисляет {fee_pct:.2f}% от каждого свапа "
        f"на наш кошелёк прямо в транзакции.\n"
        f"4. Деньги поступают мгновенно, без вывода.\n\n"
        f"Наш адрес для комиссий:\n"
        f"  {_config.referrer_address}\n\n"
        f"Математика:\n"
        f"  100 свапов по $50 в день = $50 × {fee_pct:.2f}% × 100 = "
        f"${50 * fee_pct / 100 * 100:.0f}/день\n"
        f"  1000 свапов/день = ${50 * fee_pct / 100 * 1000:.0f}/день\n\n"
        f"STON.fi объём: $3.7 млрд всего, 27 млн свапов.\n"
        f"Твоя задача — привести пользователей в бота."
    )
    await update.message.reply_text(text)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------


def build_app(config: Config, bot_wallet: WalletData) -> Application:  # type: ignore[type-arg]
    """Create and configure the Telegram Application."""
    global _config, _bot_wallet
    _config = config
    _bot_wallet = bot_wallet

    app = Application.builder().token(config.telegram_token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("tokens", cmd_tokens))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("quote", cmd_quote))
    app.add_handler(CommandHandler("swap", cmd_swap))
    app.add_handler(CommandHandler("earnings", cmd_earnings))
    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the bot.

    Единственный обязательный параметр — TELEGRAM_BOT_TOKEN в .env.
    Всё остальное настраивается автоматически:
      - TON кошелёк бота создаётся при первом запуске
      - Если REFERRER_TON_ADDRESS не задан, используется адрес кошелька бота
    """
    config = load_config()

    # Создаём или загружаем кошелёк бота (автоматически при первом запуске)
    bot_wallet = ensure_wallet()

    # Если владелец не задал свой TON адрес — комиссии идут на кошелёк бота
    if not config.referrer_address:
        import dataclasses
        config = dataclasses.replace(config, referrer_address=bot_wallet.address_hex)
        log.info(
            "REFERRER_TON_ADDRESS не задан — комиссии идут на кошелёк бота: %s",
            bot_wallet.address_bounceable,
        )

        # Сохраняем адрес в .env чтобы не генерировать снова при перезапуске
        _append_referrer_to_env(bot_wallet.address_bounceable)

    log.info("Бот запущен. Кошелёк бота: %s", bot_wallet.address_bounceable)
    log.info("Адрес для реферальных комиссий: %s", config.referrer_address)

    app = build_app(config, bot_wallet)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def _append_referrer_to_env(address: str) -> None:
    """Дописывает REFERRER_TON_ADDRESS в .env чтобы не спрашивать снова."""
    env_path = ".env"
    try:
        try:
            existing = open(env_path).read()
        except FileNotFoundError:
            existing = ""

        if "REFERRER_TON_ADDRESS" not in existing:
            with open(env_path, "a") as f:
                f.write("\n# Автоматически добавлен при первом запуске\n")
                f.write(f"REFERRER_TON_ADDRESS={address}\n")
            log.info("REFERRER_TON_ADDRESS сохранён в .env")
    except Exception as exc:
        log.warning("Не удалось записать REFERRER_TON_ADDRESS в .env: %s", exc)


if __name__ == "__main__":
    main()
