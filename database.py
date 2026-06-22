"""
SQLite база данных — пользователи, рефералы, статистика свапов.

Схема:
  users     — каждый кто написал боту
  referrals — кто кого пригласил
  swaps     — история свапов (для статистики и уровней)

Уровни (влияют на скидку к комиссии):
  Бронза  0–9   рефералов → комиссия 0.30%
  Серебро 10–29 рефералов → комиссия 0.25%
  Золото  30+   рефералов → комиссия 0.20%
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Generator

log = logging.getLogger(__name__)

DB_PATH = Path("data/bot.db")

# ---------------------------------------------------------------------------
# Уровни
# ---------------------------------------------------------------------------

LEVELS = [
    (0,  "🥉 Бронза",  30),   # (мин. рефералов, название, fee_bps)
    (10, "🥈 Серебро", 25),
    (30, "🥇 Золото",  20),
]


def get_level(referral_count: int) -> tuple[str, int]:
    """Вернуть (название уровня, fee_bps) по количеству рефералов."""
    name, fee = LEVELS[0][1], LEVELS[0][2]
    for min_refs, level_name, level_fee in LEVELS:
        if referral_count >= min_refs:
            name, fee = level_name, level_fee
    return name, fee


def next_level_info(referral_count: int) -> str:
    """Текст о следующем уровне."""
    for min_refs, level_name, level_fee in LEVELS:
        if referral_count < min_refs:
            needed = min_refs - referral_count
            return f"До {level_name}: ещё {needed} реф. → комиссия {level_fee / 100:.2f}%"
    return "Максимальный уровень достигнут!"


# ---------------------------------------------------------------------------
# Модели
# ---------------------------------------------------------------------------

@dataclass
class User:
    user_id: int
    username: str
    first_name: str
    joined_at: str
    referred_by: int | None   # user_id того кто пригласил
    referral_count: int       # сколько людей пригласил этот юзер
    swap_count: int           # сколько свапов сделал
    level_name: str
    fee_bps: int              # персональная ставка комиссии


# ---------------------------------------------------------------------------
# Подключение
# ---------------------------------------------------------------------------

@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Инициализация
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Создать таблицы если их нет."""
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id      INTEGER PRIMARY KEY,
                username     TEXT    DEFAULT '',
                first_name   TEXT    DEFAULT '',
                joined_at    TEXT    NOT NULL,
                referred_by  INTEGER REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS referrals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL REFERENCES users(user_id),
                referred_id INTEGER NOT NULL REFERENCES users(user_id),
                created_at  TEXT    NOT NULL,
                UNIQUE(referred_id)
            );

            CREATE TABLE IF NOT EXISTS swaps (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(user_id),
                from_token  TEXT    NOT NULL,
                to_token    TEXT    NOT NULL,
                amount      TEXT    NOT NULL,
                fee_earned  TEXT    NOT NULL,
                created_at  TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_referrals_referrer
                ON referrals(referrer_id);
            CREATE INDEX IF NOT EXISTS idx_swaps_user
                ON swaps(user_id);
        """)
    log.info("База данных инициализирована: %s", DB_PATH)


# ---------------------------------------------------------------------------
# Пользователи
# ---------------------------------------------------------------------------

def get_or_create_user(
    user_id: int,
    username: str = "",
    first_name: str = "",
    referred_by: int | None = None,
) -> User:
    """Вернуть пользователя или создать нового.

    Args:
        user_id:    Telegram user ID.
        username:   Telegram @username (без @).
        first_name: Имя пользователя в Telegram.
        referred_by: user_id того кто пригласил (только при первом /start).

    Returns:
        User со всеми полями включая уровень и персональную fee_bps.
    """
    now = datetime.utcnow().isoformat()

    with _conn() as con:
        existing = con.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()

        if not existing:
            con.execute(
                "INSERT INTO users(user_id, username, first_name, joined_at, referred_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, username, first_name, now, referred_by),
            )
            # Записываем реферал
            if referred_by and referred_by != user_id:
                # Проверяем что referrer существует
                referrer = con.execute(
                    "SELECT user_id FROM users WHERE user_id = ?", (referred_by,)
                ).fetchone()
                if referrer:
                    con.execute(
                        "INSERT OR IGNORE INTO referrals(referrer_id, referred_id, created_at) "
                        "VALUES (?, ?, ?)",
                        (referred_by, user_id, now),
                    )
                    log.info(
                        "Новый реферал: %s пришёл от %s", user_id, referred_by
                    )
        else:
            # Обновляем username/first_name если изменились
            con.execute(
                "UPDATE users SET username=?, first_name=? WHERE user_id=?",
                (username, first_name, user_id),
            )

        return _load_user(con, user_id)


def _load_user(con: sqlite3.Connection, user_id: int) -> User:
    """Загрузить пользователя с подсчётом рефералов и свапов."""
    row = con.execute(
        "SELECT * FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()

    referral_count = con.execute(
        "SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,)
    ).fetchone()[0]

    swap_count = con.execute(
        "SELECT COUNT(*) FROM swaps WHERE user_id = ?", (user_id,)
    ).fetchone()[0]

    level_name, fee_bps = get_level(referral_count)

    return User(
        user_id=row["user_id"],
        username=row["username"] or "",
        first_name=row["first_name"] or "",
        joined_at=row["joined_at"],
        referred_by=row["referred_by"],
        referral_count=referral_count,
        swap_count=swap_count,
        level_name=level_name,
        fee_bps=fee_bps,
    )


def get_user(user_id: int) -> User | None:
    """Вернуть пользователя или None если не найден."""
    with _conn() as con:
        row = con.execute(
            "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return None
        return _load_user(con, user_id)


# ---------------------------------------------------------------------------
# Статистика свапов
# ---------------------------------------------------------------------------

def record_swap(
    user_id: int,
    from_token: str,
    to_token: str,
    amount: str,
    fee_earned: str,
) -> None:
    """Записать выполненный свап."""
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        con.execute(
            "INSERT INTO swaps(user_id, from_token, to_token, amount, fee_earned, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, from_token, to_token, amount, fee_earned, now),
        )


# ---------------------------------------------------------------------------
# Топ рефереров
# ---------------------------------------------------------------------------

@dataclass
class LeaderEntry:
    rank: int
    first_name: str
    username: str
    referral_count: int
    swap_count: int
    level_name: str


def get_leaderboard(limit: int = 10) -> list[LeaderEntry]:
    """Топ-N пользователей по количеству рефералов."""
    with _conn() as con:
        rows = con.execute("""
            SELECT
                u.user_id,
                u.first_name,
                u.username,
                COUNT(r.referred_id) AS ref_count,
                (SELECT COUNT(*) FROM swaps s WHERE s.user_id = u.user_id) AS swap_count
            FROM users u
            LEFT JOIN referrals r ON r.referrer_id = u.user_id
            GROUP BY u.user_id
            ORDER BY ref_count DESC, swap_count DESC
            LIMIT ?
        """, (limit,)).fetchall()

    result: list[LeaderEntry] = []
    for rank, row in enumerate(rows, start=1):
        level_name, _ = get_level(row["ref_count"])
        result.append(LeaderEntry(
            rank=rank,
            first_name=row["first_name"] or "Аноним",
            username=row["username"] or "",
            referral_count=row["ref_count"],
            swap_count=row["swap_count"],
            level_name=level_name,
        ))
    return result


# ---------------------------------------------------------------------------
# Глобальная статистика
# ---------------------------------------------------------------------------

@dataclass
class GlobalStats:
    total_users: int
    total_swaps: int
    total_referrals: int


def get_global_stats() -> GlobalStats:
    """Общая статистика бота."""
    with _conn() as con:
        total_users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_swaps = con.execute("SELECT COUNT(*) FROM swaps").fetchone()[0]
        total_referrals = con.execute("SELECT COUNT(*) FROM referrals").fetchone()[0]
    return GlobalStats(
        total_users=total_users,
        total_swaps=total_swaps,
        total_referrals=total_referrals,
    )
