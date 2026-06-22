"""
Telegram bot — STON.fi swap aggregator with referral program.

Реферальная программа:
  - Каждый пользователь получает уникальную ссылку /ref
  - За каждого приглашённого пользователь повышает уровень
  - Уровни дают скидку к комиссии свапа:
      Бронза  (0–9 рефералов)  → 0.30% комиссия
      Серебро (10–29 рефералов) → 0.25% комиссия
      Золото  (30+ рефералов)  → 0.20% комиссия

Команды:
    /start   — Приветствие (принимает реферальный код)
    /help    — Список команд
    /tokens  — Поддерживаемые токены
    /balance — Баланс кошелька бота
    /quote   — Котировка без исполнения
    /swap    — Выполнить свап
    /ref     — Твоя реферальная ссылка и статистика
    /top     — Топ рефереров
    /stats   — Глобальная статистика бота
    /earnings — Как работает заработок
"""

from __future__ import annotations

import dataclasses
import logging
from decimal import Decimal, InvalidOperation

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import database as db
from config import Config, load_config
from omniston import TOKENS, build_transfer, request_quote
from ton_wallet import (
    WalletData,
    ensure_wallet,
    fetch_balance,
    sign_and_send_messages,
)

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
_bot_username: str = ""   # заполняется при старте из bot.get_me()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _referral_link(user_id: int) -> str:
    """Сформировать реферальную ссылку для пользователя."""
    if _bot_username:
        return f"https://t.me/{_bot_username}?start=ref_{user_id}"
    return "(запусти бота чтобы получить ссылку, username ещё не загружен)"


def _parse_swap_args(args: list[str]) -> tuple[Decimal, str, str]:
    """Разобрать аргументы: <сумма> <ОТ> <К>.

    Raises:
        ValueError: если аргументы неверные.
    """
    if len(args) < 3:
        raise ValueError(
            "Нужно 3 аргумента: <сумма> <откуда> <куда>\nПример: /swap 1 TON USDT"
        )
    try:
        amount = Decimal(args[0].replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"Неверная сумма: {args[0]}") from exc
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля.")
    from_token = args[1].upper()
    to_token = args[2].upper()
    if from_token not in TOKENS:
        raise ValueError(
            f"Неизвестный токен: {from_token}. /tokens — список доступных."
        )
    if to_token not in TOKENS:
        raise ValueError(
            f"Неизвестный токен: {to_token}. /tokens — список доступных."
        )
    if from_token == to_token:
        raise ValueError("Токены должны быть разными.")
    return amount, from_token, to_token


def _get_user_fee_bps(telegram_user_id: int) -> int:
    """Вернуть персональную ставку комиссии пользователя на основе его уровня."""
    user = db.get_user(telegram_user_id)
    if user:
        return user.fee_bps
    return _config.referrer_fee_bps


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Приветствие. Принимает реферальный код вида /start ref_12345."""
    tg_user = update.effective_user
    if not tg_user:
        return

    # Парсим реферальный код из аргумента /start ref_USERID
    referred_by: int | None = None
    args: list[str] = context.args or []  # type: ignore[assignment]
    if args and args[0].startswith("ref_"):
        try:
            referred_by = int(args[0][4:])
        except ValueError:
            pass

    # Регистрируем пользователя (или обновляем если уже есть)
    user = db.get_or_create_user(
        user_id=tg_user.id,
        username=tg_user.username or "",
        first_name=tg_user.first_name or "",
        referred_by=referred_by,
    )

    ref_link = _referral_link(tg_user.id)

    # Приветствие для новых пользователей включает реферальную программу
    is_new = referred_by is not None
    bonus_text = (
        "\n✅ Ты пришёл по реферальной ссылке — добро пожаловать!\n"
        if is_new
        else ""
    )

    text = (
        f"Привет, {tg_user.first_name}! Я TON Swap Bot.\n\n"
        f"{bonus_text}"
        f"Обменивай токены с лучшим курсом через STON.fi.\n\n"
        f"Твой уровень: {user.level_name}\n"
        f"Комиссия свапа: {user.fee_bps / 100:.2f}%\n\n"
        f"Пригласи друзей и получай скидку на комиссию:\n"
        f"{ref_link}\n\n"
        f"Команды: /help"
    )
    await update.message.reply_text(text)  # type: ignore[union-attr]


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Список всех команд."""
    text = (
        "Команды:\n\n"
        "Свапы:\n"
        "  /quote <сумма> <ОТ> <К>  — котировка без исполнения\n"
        "  /swap  <сумма> <ОТ> <К>  — выполнить обмен\n"
        "  Пример: /swap 1 TON USDT\n\n"
        "Реферальная программа:\n"
        "  /ref    — твоя ссылка + статистика рефералов\n"
        "  /top    — топ рефереров\n\n"
        "Информация:\n"
        "  /tokens   — поддерживаемые токены\n"
        "  /balance  — баланс бота\n"
        "  /stats    — статистика бота\n"
        "  /earnings — как работает заработок\n"
    )
    await update.message.reply_text(text)  # type: ignore[union-attr]


async def cmd_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Список поддерживаемых токенов."""
    lines = [f"  {sym} — {addr[:12]}..." for sym, addr in TOKENS.items()]
    text = "Поддерживаемые токены:\n\n" + "\n".join(lines)
    await update.message.reply_text(text)  # type: ignore[union-attr]


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Баланс кошелька бота."""
    await update.message.reply_text("Проверяю баланс...")  # type: ignore[union-attr]
    balance = await fetch_balance(
        _bot_wallet.address_hex,
        api_key=_config.toncenter_api_key,
        testnet=_config.use_sandbox,
    )
    global_stats = db.get_global_stats()
    text = (
        f"Кошелёк бота:\n"
        f"  Адрес: {_bot_wallet.address_bounceable}\n"
        f"  Баланс: {balance:.4f} TON\n\n"
        f"Статистика:\n"
        f"  Пользователей: {global_stats.total_users}\n"
        f"  Свапов всего: {global_stats.total_swaps}\n"
        f"  Рефералов: {global_stats.total_referrals}"
    )
    await update.message.reply_text(text)  # type: ignore[union-attr]


async def cmd_ref(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать реферальную ссылку пользователя и его статистику."""
    tg_user = update.effective_user
    if not tg_user:
        return

    user = db.get_or_create_user(
        user_id=tg_user.id,
        username=tg_user.username or "",
        first_name=tg_user.first_name or "",
    )

    ref_link = _referral_link(tg_user.id)
    next_level = db.next_level_info(user.referral_count)

    text = (
        f"Твоя реферальная программа:\n\n"
        f"Уровень: {user.level_name}\n"
        f"Комиссия твоих свапов: {user.fee_bps / 100:.2f}%\n"
        f"Приглашено: {user.referral_count} чел.\n"
        f"Твоих свапов: {user.swap_count}\n\n"
        f"{next_level}\n\n"
        f"Твоя ссылка:\n"
        f"{ref_link}\n\n"
        f"Как работает:\n"
        f"  Бронза  (0–9 рефералов)  → 0.30% комиссия\n"
        f"  Серебро (10–29 рефералов) → 0.25% комиссия\n"
        f"  Золото  (30+ рефералов)  → 0.20% комиссия\n\n"
        f"Поделись ссылкой — чем больше пользователей, тем меньше платишь!"
    )
    await update.message.reply_text(text)  # type: ignore[union-attr]


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Топ-10 рефереров."""
    leaders = db.get_leaderboard(limit=10)

    if not leaders:
        await update.message.reply_text(  # type: ignore[union-attr]
            "Пока нет данных. Стань первым — поделись своей ссылкой /ref!"
        )
        return

    lines = ["Топ рефереров:\n"]
    medals = ["🥇", "🥈", "🥉"]
    for entry in leaders:
        medal = medals[entry.rank - 1] if entry.rank <= 3 else f"  {entry.rank}."
        name = entry.first_name
        if entry.username:
            name = f"@{entry.username}"
        lines.append(
            f"{medal} {name} — {entry.referral_count} рефералов "
            f"· {entry.swap_count} свапов {entry.level_name}"
        )

    await update.message.reply_text("\n".join(lines))  # type: ignore[union-attr]


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальная статистика бота."""
    stats = db.get_global_stats()
    fee_pct = _config.referrer_fee_bps / 100
    text = (
        f"Статистика бота:\n\n"
        f"  Пользователей: {stats.total_users}\n"
        f"  Свапов выполнено: {stats.total_swaps}\n"
        f"  Рефералов зарегистрировано: {stats.total_referrals}\n\n"
        f"  Реф. комиссия: {fee_pct:.2f}% с каждого свапа\n"
        f"  Основана на STON.fi Omniston\n"
        f"  Объём STON.fi: $3.7 млрд всего"
    )
    await update.message.reply_text(text)  # type: ignore[union-attr]


async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Котировка без исполнения свапа."""
    tg_user = update.effective_user
    args: list[str] = context.args or []  # type: ignore[assignment]
    try:
        amount, from_token, to_token = _parse_swap_args(args)
    except ValueError as exc:
        await update.message.reply_text(f"Ошибка: {exc}")  # type: ignore[union-attr]
        return

    # Используем персональную ставку пользователя
    fee_bps = _get_user_fee_bps(tg_user.id) if tg_user else _config.referrer_fee_bps

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
            referrer_fee_bps=fee_bps,
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
    """Выполнить свап с кошелька бота."""
    tg_user = update.effective_user
    args: list[str] = context.args or []  # type: ignore[assignment]
    try:
        amount, from_token, to_token = _parse_swap_args(args)
    except ValueError as exc:
        await update.message.reply_text(f"Ошибка: {exc}")  # type: ignore[union-attr]
        return

    # Регистрируем пользователя если впервые
    if tg_user:
        db.get_or_create_user(
            user_id=tg_user.id,
            username=tg_user.username or "",
            first_name=tg_user.first_name or "",
        )

    # Персональная ставка на основе уровня пользователя
    fee_bps = _get_user_fee_bps(tg_user.id) if tg_user else _config.referrer_fee_bps

    await update.message.reply_text(  # type: ignore[union-attr]
        f"Запрашиваю лучший курс для {amount} {from_token} → {to_token}..."
    )

    # Шаг 1: Котировка
    try:
        quote = await request_quote(
            from_token=from_token,
            to_token=to_token,
            from_amount=amount,
            wallet_address=_bot_wallet.address_hex,
            referrer_address=_config.referrer_address,
            referrer_fee_bps=fee_bps,
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

    # Шаг 2: Построение транзакции
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

    # Шаг 3: Подпись и отправка
    ok = await sign_and_send_messages(
        transfer.messages,
        _bot_wallet,
        api_key=_config.toncenter_api_key,
        testnet=_config.use_sandbox,
    )

    if ok:
        fee_pct = fee_bps / 100
        fee_amount = quote.fee_amount_human

        # Записываем свап в базу
        if tg_user:
            db.record_swap(
                user_id=tg_user.id,
                from_token=from_token,
                to_token=to_token,
                amount=str(amount),
                fee_earned=str(fee_amount),
            )
            # Обновляем статистику пользователя для /ref
            updated = db.get_user(tg_user.id)
            level_text = (
                f"\nТвой уровень: {updated.level_name} "
                f"({updated.swap_count} свапов, {updated.referral_count} рефералов)"
                if updated
                else ""
            )
        else:
            level_text = ""

        await update.message.reply_text(  # type: ignore[union-attr]
            f"Свап выполнен!\n\n"
            f"{quote.summary()}\n\n"
            f"Комиссия {fee_pct:.2f}% ({fee_amount} {from_token}) "
            f"зачислена автоматически."
            f"{level_text}\n\n"
            f"Пригласи друзей → /ref → получай скидку на следующие свапы!"
        )
    else:
        await update.message.reply_text(  # type: ignore[union-attr]
            "Транзакция отправлена, статус неизвестен. "
            "Проверь на https://tonscan.org"
        )


async def cmd_earnings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Объяснение бизнес-модели."""
    fee_pct = _config.referrer_fee_bps / 100
    text = (
        f"Как работает заработок:\n\n"
        f"1. Пользователь делает свап через бота.\n"
        f"2. Бот отправляет запрос в STON.fi Omniston с нашим "
        f"referrer_address.\n"
        f"3. STON.fi зачисляет {fee_pct:.2f}% на наш кошелёк в той же "
        f"транзакции — автоматически.\n\n"
        f"Реферальная программа (для роста аудитории):\n"
        f"  Пользователь приглашает друзей → растёт уровень → "
        f"платит меньше комиссии.\n"
        f"  Больше пользователей = больше свапов = больше дохода.\n\n"
        f"Математика:\n"
        f"  100 свапов/день × $50 = ${50 * fee_pct / 100 * 100:.0f}/день\n"
        f"  1000 свапов/день × $50 = ${50 * fee_pct / 100 * 1000:.0f}/день\n\n"
        f"Адрес для комиссий:\n  {_config.referrer_address}"
    )
    await update.message.reply_text(text)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------


def build_app(config: Config, bot_wallet: WalletData) -> Application:  # type: ignore[type-arg]
    """Создать и настроить Telegram Application."""
    global _config, _bot_wallet
    _config = config
    _bot_wallet = bot_wallet

    app = Application.builder().token(config.telegram_token).build()
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("tokens",   cmd_tokens))
    app.add_handler(CommandHandler("balance",  cmd_balance))
    app.add_handler(CommandHandler("quote",    cmd_quote))
    app.add_handler(CommandHandler("swap",     cmd_swap))
    app.add_handler(CommandHandler("ref",      cmd_ref))
    app.add_handler(CommandHandler("top",      cmd_top))
    app.add_handler(CommandHandler("stats",    cmd_stats))
    app.add_handler(CommandHandler("earnings", cmd_earnings))
    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Запустить бота.

    Единственный обязательный параметр — TELEGRAM_BOT_TOKEN в .env.
    Всё остальное настраивается автоматически.
    """
    global _bot_username

    config = load_config()

    # Инициализируем базу данных
    db.init_db()

    # Создаём или загружаем кошелёк бота
    bot_wallet = ensure_wallet()

    # Если REFERRER_TON_ADDRESS не задан — используем кошелёк бота
    if not config.referrer_address:
        config = dataclasses.replace(
            config, referrer_address=bot_wallet.address_hex
        )
        log.info(
            "REFERRER_TON_ADDRESS не задан — комиссии идут на кошелёк бота: %s",
            bot_wallet.address_bounceable,
        )
        _append_referrer_to_env(bot_wallet.address_bounceable)

    log.info("Бот запущен. Кошелёк: %s", bot_wallet.address_bounceable)
    log.info("Адрес для комиссий: %s", config.referrer_address)

    app = build_app(config, bot_wallet)

    # Получаем username бота для реферальных ссылок
    async def _on_startup(application: Application) -> None:  # type: ignore[type-arg]
        global _bot_username
        me = await application.bot.get_me()
        _bot_username = me.username or ""
        log.info("Username бота: @%s", _bot_username)

    app.post_init = _on_startup  # type: ignore[method-assign]
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def _append_referrer_to_env(address: str) -> None:
    """Дописать REFERRER_TON_ADDRESS в .env."""
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
