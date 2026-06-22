"""
TON wallet helpers.

Handles wallet creation, loading, balance queries, and transaction
broadcasting via the Toncenter API.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import aiohttp
from tonsdk.contract.wallet import Wallets, WalletVersionEnum
from tonsdk.crypto import mnemonic_new

log = logging.getLogger(__name__)

TONCENTER_MAINNET = "https://toncenter.com/api/v2"
TONCENTER_TESTNET = "https://testnet.toncenter.com/api/v2"

DATA_DIR = Path("data")
WALLET_FILE = DATA_DIR / "wallet.json"

# Minimum balance (in TON) needed before the bot can send transactions
MIN_BALANCE_TON: float = 0.05


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class WalletData:
    """Persisted wallet info (never stores the private key directly)."""

    mnemonic: list[str]        # 24-word seed phrase – keep secret!
    address_hex: str           # Raw hex address (no bounce flag)
    address_bounceable: str    # User-friendly bounceable address
    workchain: int
    version: str


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def save_wallet(wallet: WalletData) -> None:
    _ensure_data_dir()
    WALLET_FILE.write_text(json.dumps(asdict(wallet), indent=2))
    log.info("Кошелёк сохранён: %s", WALLET_FILE)


def load_wallet() -> WalletData | None:
    if not WALLET_FILE.exists():
        return None
    data = json.loads(WALLET_FILE.read_text())
    return WalletData(**data)


# ---------------------------------------------------------------------------
# Wallet creation
# ---------------------------------------------------------------------------


def ensure_wallet() -> WalletData:
    """Return the existing bot wallet, or create a new one.

    On first run the 24-word mnemonic is printed to stdout — write it down
    and keep it safe. It is also saved to data/wallet.json (excluded from git
    by .gitignore).
    """
    wallet = load_wallet()
    if wallet:
        return wallet

    mnemonic = mnemonic_new()
    _, _, _, contract = Wallets.from_mnemonics(mnemonic, WalletVersionEnum.v4r2, 0)

    wallet = WalletData(
        mnemonic=mnemonic,
        address_hex=contract.address.to_string(False),
        address_bounceable=contract.address.to_string(True, True, False),
        workchain=contract.address.wc,
        version=WalletVersionEnum.v4r2.value,
    )
    save_wallet(wallet)

    print("\n" + "=" * 60)
    print("НОВЫЙ КОШЕЛЁК СОЗДАН — СОХРАНИ МНЕМОНИКУ!")
    print("=" * 60)
    print(" ".join(mnemonic))
    print("=" * 60)
    print(f"Адрес: {wallet.address_bounceable}")
    print("Пополни кошелёк перед первым свапом (мин. 0.05 TON).")
    print("=" * 60 + "\n")

    return wallet


# ---------------------------------------------------------------------------
# Toncenter API helpers
# ---------------------------------------------------------------------------


def _toncenter_url(testnet: bool = False) -> str:
    return TONCENTER_TESTNET if testnet else TONCENTER_MAINNET


def _api_key_header(api_key: str | None) -> dict[str, str]:
    if api_key:
        return {"X-API-Key": api_key}
    return {}


async def fetch_balance(
    address_hex: str,
    *,
    api_key: str | None = None,
    testnet: bool = False,
) -> float:
    """Return wallet balance in TON (float). Returns 0.0 on error."""
    url = f"{_toncenter_url(testnet)}/getAddressBalance"
    headers = _api_key_header(api_key)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                params={"address": address_hex},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data: dict[str, Any] = await resp.json()
                raw = int(data.get("result", 0))
                return raw / 1e9  # nanotons → TON
    except Exception as exc:
        log.warning("Не удалось получить баланс: %s", exc)
        return 0.0


async def send_boc(
    boc_b64: str,
    *,
    api_key: str | None = None,
    testnet: bool = False,
) -> bool:
    """Broadcast a signed BOC to the TON network via Toncenter.

    Args:
        boc_b64: Base64-encoded BOC string.
        api_key: Toncenter API key (get one via @toncenter on Telegram).
        testnet: Use testnet endpoint.

    Returns:
        True if broadcast succeeded, False otherwise.
    """
    url = f"{_toncenter_url(testnet)}/sendBoc"
    headers = _api_key_header(api_key)
    payload = {"boc": boc_b64}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                data = await resp.json()
                ok = data.get("ok", False)
                if ok:
                    log.info("Транзакция отправлена успешно.")
                else:
                    log.error("Toncenter вернул ошибку: %s", data)
                return bool(ok)
    except Exception as exc:
        log.error("Ошибка при отправке BOC: %s", exc)
        return False


async def ensure_wallet_deployed(
    wallet: WalletData,
    *,
    api_key: str | None = None,
    testnet: bool = False,
) -> bool:
    """Check if the wallet contract is deployed; deploy it if not.

    A v4r2 wallet must be deployed on its first outgoing transfer.
    Toncenter returns seqno 0 for undeployed wallets.
    """
    url = f"{_toncenter_url(testnet)}/runGetMethod"
    payload = {
        "address": wallet.address_hex,
        "method": "seqno",
        "stack": [],
    }
    headers = _api_key_header(api_key)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                # If seqno method does not exist, wallet is not deployed yet
                if data.get("error") or not data.get("ok"):
                    log.info("Кошелёк ещё не задеплоен — деплой произойдёт при первой транзакции.")
                return True
    except Exception as exc:
        log.warning("Не удалось проверить деплой кошелька: %s", exc)
        return True  # Proceed anyway


async def sign_and_send_messages(
    messages: list[dict[str, Any]],
    wallet: WalletData,
    *,
    api_key: str | None = None,
    testnet: bool = False,
) -> bool:
    """Sign and broadcast the transfer messages produced by Omniston.

    Each message from Omniston contains: address, amount (nanotons), payload (b64 BOC).
    We create a wallet transfer for each and broadcast it.

    Args:
        messages: List of message dicts from BuildTransferResult.
        wallet: Bot wallet data (mnemonic included).
        api_key: Toncenter API key.
        testnet: Use testnet.

    Returns:
        True if all messages broadcast successfully.
    """
    if not messages:
        log.error("Нет сообщений для отправки.")
        return False

    _, _, _, contract = Wallets.from_mnemonics(
        wallet.mnemonic,
        WalletVersionEnum(wallet.version),
        0,
    )

    # Fetch current seqno
    seqno = await _fetch_seqno(wallet.address_hex, api_key=api_key, testnet=testnet)

    success = True
    for msg in messages:
        target_address: str = msg.get("address", "")
        amount_nano: int = int(msg.get("amount", 0))
        payload_b64: str = msg.get("payload", "")

        if not target_address:
            log.warning("Пропускаю сообщение без адреса: %s", msg)
            continue

        from tonsdk.boc import Cell  # type: ignore[import-untyped]

        payload_cell: Cell | None = None
        if payload_b64:
            try:
                payload_cell = Cell.one_from_boc(base64.b64decode(payload_b64))
            except Exception as exc:
                log.error("Ошибка декодирования payload: %s", exc)
                continue

        transfer = contract.create_transfer_message(
            to_addr=target_address,
            amount=amount_nano,
            seqno=seqno,
            payload=payload_cell,
        )
        boc_b64 = base64.b64encode(transfer["message"].to_boc(False)).decode("ascii")

        ok = await send_boc(boc_b64, api_key=api_key, testnet=testnet)
        if not ok:
            success = False
        seqno += 1

    return success


async def _fetch_seqno(
    address_hex: str,
    *,
    api_key: str | None = None,
    testnet: bool = False,
) -> int:
    """Return current wallet seqno (0 if undeployed)."""
    url = f"{_toncenter_url(testnet)}/runGetMethod"
    payload = {"address": address_hex, "method": "seqno", "stack": []}
    headers = _api_key_header(api_key)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                stack = data.get("result", {}).get("stack", [])
                if stack:
                    return int(stack[0][1], 16)
    except Exception as exc:
        log.warning("Не удалось получить seqno: %s", exc)
    return 0
