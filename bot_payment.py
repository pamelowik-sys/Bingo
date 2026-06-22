"""
TON Payment Fee Bot
====================
Скрипт принимает платежи через TON блокчейн,
берёт комиссию 0.5% с каждой транзакции и
пересылает остаток получателю.

Требования:
    pip install tonsdk aiohttp python-dotenv

Переменные окружения (.env файл):
    WALLET_MNEMONIC=слово1 слово2 ... слово24
    COMMISSION_PERCENT=0.5
    RECEIVER_ADDRESS=адрес_получателя
"""

import asyncio
import os
import logging
from decimal import Decimal
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Настройка логов
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

load_dotenv()

# ---------------------------------------------------------------------------
# Конфиг
# ---------------------------------------------------------------------------
MNEMONIC: list[str] = os.getenv("WALLET_MNEMONIC", "").split()
COMMISSION_PERCENT: Decimal = Decimal(os.getenv("COMMISSION_PERCENT", "0.5"))
RECEIVER_ADDRESS: str = os.getenv("RECEIVER_ADDRESS", "")

# Минимальная сумма перевода в TON (меньше не имеет смысла из-за сетевых сборов)
MIN_TRANSFER_TON: Decimal = Decimal("0.05")


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def calculate_fee(amount_ton: Decimal) -> tuple[Decimal, Decimal]:
    """Рассчитывает комиссию и сумму к переводу.

    Args:
        amount_ton: Сумма входящего платежа в TON.

    Returns:
        Кортеж (комиссия, сумма_получателю).
    """
    fee = (amount_ton * COMMISSION_PERCENT / Decimal("100")).quantize(Decimal("0.000000001"))
    net = amount_ton - fee
    return fee, net


def validate_config() -> None:
    """Проверяет что все переменные окружения заданы."""
    if not MNEMONIC or len(MNEMONIC) != 24:
        raise ValueError("WALLET_MNEMONIC должна содержать 24 слова")
    if not RECEIVER_ADDRESS:
        raise ValueError("RECEIVER_ADDRESS не задан")
    if COMMISSION_PERCENT <= 0 or COMMISSION_PERCENT > 10:
        raise ValueError("COMMISSION_PERCENT должен быть от 0 до 10")


# ---------------------------------------------------------------------------
# Основная логика
# ---------------------------------------------------------------------------

class FeeCollector:
    """Обрабатывает входящие TON-транзакции и забирает комиссию."""

    def __init__(self) -> None:
        validate_config()
        self.total_collected: Decimal = Decimal("0")
        self.tx_count: int = 0

    async def process_transaction(
        self,
        sender_address: str,
        amount_ton: Decimal,
        comment: str = "",
    ) -> dict[str, str]:
        """Обрабатывает одну входящую транзакцию.

        Args:
            sender_address: Адрес отправителя.
            amount_ton: Сумма в TON.
            comment: Необязательный комментарий к транзакции.

        Returns:
            Словарь с деталями обработки.
        """
        if amount_ton < MIN_TRANSFER_TON:
            log.warning(
                "Транзакция от %s слишком маленькая: %s TON (минимум %s TON)",
                sender_address,
                amount_ton,
                MIN_TRANSFER_TON,
            )
            return {
                "status": "skipped",
                "reason": f"Сумма {amount_ton} TON меньше минимума {MIN_TRANSFER_TON} TON",
            }

        fee, net_amount = calculate_fee(amount_ton)

        log.info(
            "Транзакция от %s | Сумма: %s TON | Комиссия: %s TON | К переводу: %s TON",
            sender_address,
            amount_ton,
            fee,
            net_amount,
        )

        # TODO: Здесь подключить реальный TON SDK для отправки
        # from tonsdk.contract.wallet import Wallets, WalletVersionEnum
        # wallet = Wallets.from_mnemonics(MNEMONIC, WalletVersionEnum.v4r2, 0)
        # await wallet.transfer(to=RECEIVER_ADDRESS, amount=int(net_amount * 1e9), ...)

        self.total_collected += fee
        self.tx_count += 1

        return {
            "status": "processed",
            "sender": sender_address,
            "amount_ton": str(amount_ton),
            "fee_ton": str(fee),
            "net_ton": str(net_amount),
            "receiver": RECEIVER_ADDRESS,
            "comment": comment,
            "total_fees_earned": str(self.total_collected),
        }

    def print_stats(self) -> None:
        """Выводит статистику заработка."""
        log.info(
            "=== Статистика === Транзакций: %d | Собрано комиссий: %s TON",
            self.tx_count,
            self.total_collected,
        )


# ---------------------------------------------------------------------------
# Демо-запуск (симуляция без реального блокчейна)
# ---------------------------------------------------------------------------

async def demo() -> None:
    """Симулирует несколько входящих платежей для демонстрации."""
    collector = FeeCollector()

    test_transactions = [
        ("EQA1...abc", Decimal("1.0"), "оплата заказа #1"),
        ("EQB2...def", Decimal("5.5"), "оплата заказа #2"),
        ("EQC3...ghi", Decimal("0.02"), "слишком маленький платёж"),
        ("EQD4...jkl", Decimal("100.0"), "крупный перевод"),
    ]

    for sender, amount, comment in test_transactions:
        result = await collector.process_transaction(sender, amount, comment)
        log.info("Результат: %s", result)

    collector.print_stats()


if __name__ == "__main__":
    asyncio.run(demo())
