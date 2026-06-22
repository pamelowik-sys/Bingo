# Документация возможностей — STON.fi Omniston Telegram Bot

## Что умеет бот

Telegram бот для обмена токенов в TON блокчейне с автоматическим
сбором реферальных комиссий через протокол STON.fi Omniston.

---

## Команды

### `/start`
Приветственное сообщение с кратким описанием и навигацией.

### `/help`
Полный список команд с примерами использования.

### `/tokens`
Список поддерживаемых токенов и их адреса на TON.

**Текущие токены:**

| Символ | Описание |
|--------|----------|
| TON    | Нативный токен TON блокчейна |
| USDT   | Tether USD (TRC-20 на TON) |
| STON   | Токен платформы STON.fi |
| NOT    | Notcoin |
| USDC   | USD Coin |

Добавить новый токен: в `omniston.py` → словарь `TOKENS` и `TOKEN_DECIMALS`.

### `/balance`
Показывает:
- Адрес кошелька бота
- Баланс в TON
- Адрес для получения реферальных комиссий
- Текущий процент комиссии

### `/quote <сумма> <ОТ> <К>`
Запрашивает лучший курс у STON.fi Omniston **без исполнения** свапа.

```
/quote 1 TON USDT
/quote 100 USDT TON
/quote 5 TON STON
```

Показывает:
- Сколько получит пользователь
- Курс обмена
- Размер нашей реферальной комиссии

### `/swap <сумма> <ОТ> <К>`
Полный цикл: котировка → построение транзакции → подпись → отправка.

```
/swap 1 TON USDT
/swap 50 USDT TON
```

Шаги выполнения:
1. Запрос котировки через WebSocket у Omniston
2. Выбор лучшего маршрута (STON.fi v1/v2, DeDust, Tonco, Escrow)
3. Построение транзакции через `v1beta7.transaction.build_transfer`
4. Подпись кошельком бота (v4r2)
5. Отправка через Toncenter API
6. Подтверждение пользователю

### `/earnings`
Объяснение бизнес-модели с математикой заработка.

---

## Как работает заработок на комиссиях

### Механизм

```
Пользователь: /swap 100 USDT TON
                  │
                  ▼
       Бот → STON.fi Omniston WebSocket
             {
               referrer_address: "EQ...(наш кошелёк)",
               referrer_fee_bps: 30  ← 0.30%
             }
                  │
                  ▼
       STON.fi находит лучший маршрут
       и встраивает нашу комиссию в транзакцию
                  │
                  ▼
       Транзакция исполняется:
         Пользователь получил TON
         Наш кошелёк получил 0.30 USDT автоматически
```

### Ставки комиссии по DEX

| DEX | Макс. ставка | Метод получения |
|-----|-------------|-----------------|
| STON.fi DEX v1 | 0.10% | Автоматически в транзакции |
| STON.fi DEX v2 | 0.01% – 1.00% | Vault (накапливается, вывод вручную) |
| DeDust | 0.01% – 1.00% | Vault |
| Tonco | 0.01% – 1.00% | Vault |
| Escrow | 0.01% – 1.00% | Vault |

> `flexible_referrer_fee: true` позволяет протоколу снижать ставку до 0.10%
> на маршрутах через DEX v1, чтобы пользователь получил лучший курс.
> На всех остальных DEX применяется полная ставка из конфига.

### Вывод накопленных комиссий (DEX v2 и выше)

Комиссии с DEX v2, DeDust, Tonco и Escrow накапливаются в Vault-контрактах.
Вывести их можно двумя способами:

**Через веб-интерфейс:**
1. Открой https://sdk-demo-app.ston.fi/vault
2. Подключи кошелёк
3. Нажми "Claim" напротив каждого токена

**Через код (автоматически):**
В `omniston.py` есть заглушка — можно дописать автовывод через SDK.

---

## Конфигурация (.env)

| Переменная | Описание | По умолчанию |
|-----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Токен от @BotFather | **обязательно** |
| `REFERRER_TON_ADDRESS` | Твой TON кошелёк для комиссий | **обязательно** |
| `TONCENTER_API_KEY` | Ключ API Toncenter | пусто (лимит 1 req/s) |
| `REFERRER_FEE_BPS` | Комиссия в basis points | `30` (0.30%) |
| `SLIPPAGE_BPS` | Макс. проскальзывание | `100` (1%) |
| `USE_SANDBOX` | Тестовая сеть | `false` |

---

## Архитектура кода

```
Bingo/
├── setup.sh          ← Автоустановка и запуск (./setup.sh)
├── telegram_bot.py   ← Точка входа, Telegram handlers
├── omniston.py       ← STON.fi Omniston WebSocket клиент
├── ton_wallet.py     ← TON кошелёк, Toncenter API
├── config.py         ← Загрузка и валидация .env
├── requirements.txt  ← Зависимости
├── .env.example      ← Шаблон конфигурации
├── .env              ← Конфиг (НЕ в git)
├── data/
│   └── wallet.json   ← Кошелёк бота (НЕ в git)
├── CONVERSATION.md   ← История работы с Neo
└── CAPABILITIES.md   ← Этот файл
```

### Зависимости

| Пакет | Версия | Зачем |
|-------|--------|-------|
| python-telegram-bot | 21.5 | Telegram Bot API, async polling |
| tonsdk | 1.0.13 | Генерация кошелька, подпись BOC |
| websockets | 11–12 | WebSocket соединение с Omniston |
| aiohttp | 3.9 | HTTP к Toncenter API |
| python-dotenv | 1.0 | Загрузка .env |

---

## Безопасность

| Что | Как защищено |
|-----|-------------|
| .env файл | В .gitignore — никогда не попадает в git |
| data/wallet.json | В .gitignore — мнемоника только локально |
| Приватный ключ | Никогда не передаётся по сети |
| Toncenter API key | Только в .env, не в коде |

**Никогда не делай:**
- Не публикуй .env в GitHub
- Не отправляй мнемонику никому
- Не запускай бота на чужом сервере без доверия

---

## Масштабирование и развитие

### Добавить новый токен

```python
# omniston.py
TOKENS["DOGS"] = "EQC...адрес_токена"
TOKEN_DECIMALS["DOGS"] = 9
```

### Увеличить комиссию

```bash
# .env
REFERRER_FEE_BPS=50  # 0.50% (максимум эффективно: 100 = 1%)
```

### Добавить пользовательские кошельки (TonConnect)

Сейчас бот свапает со своего кошелька. Для свапов с кошельков пользователей
нужно добавить TonConnect: https://docs.ton.org/develop/dapps/ton-connect/overview

Это позволит:
- Убрать необходимость держать баланс на боте
- Зарабатывать реф. комиссию с объёма пользователей
- Масштабироваться без ограничений по балансу

### Webhook вместо polling

Для production-сервера замени в `telegram_bot.py`:
```python
# Вместо:
app.run_polling(allowed_updates=Update.ALL_TYPES)

# Используй:
app.run_webhook(
    listen="0.0.0.0",
    port=8443,
    url_path=config.telegram_token,
    webhook_url=f"https://your-domain.com/{config.telegram_token}",
)
```

### Автоматический вывод комиссий с Vault

Добавить в `omniston.py` функцию `withdraw_vault_fees()` с использованием
STON.fi SDK: https://github.com/ston-fi/sdk/tree/main/examples/next-js-app/app/vault

---

## Запуск на сервере (VPS)

### Автоустановка (рекомендуется)
```bash
git clone https://github.com/pamelowik-sys/Bingo.git
cd Bingo
chmod +x setup.sh
./setup.sh
```

### Запуск как systemd-сервис (работает 24/7)

```ini
# /etc/systemd/system/stonfi-bot.service
[Unit]
Description=STON.fi Omniston Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/Bingo
ExecStart=/home/ubuntu/Bingo/.venv/bin/python telegram_bot.py
Restart=always
RestartSec=10
EnvironmentFile=/home/ubuntu/Bingo/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable stonfi-bot
sudo systemctl start stonfi-bot
sudo systemctl status stonfi-bot
```

### Минимальный VPS
- RAM: 512 MB
- CPU: 1 ядро
- OS: Ubuntu 22.04
- Стоимость: от $4/месяц (Hetzner, DigitalOcean, Timeweb)

---

## Потенциальный доход

| Свапов/день | Средняя сумма | Комиссия 0.3% | В месяц |
|-------------|--------------|---------------|---------|
| 100 | $50 | $15/день | **$450** |
| 500 | $50 | $75/день | **$2 250** |
| 1 000 | $50 | $150/день | **$4 500** |
| 5 000 | $100 | $1 500/день | **$45 000** |
| 10 000 | $100 | $3 000/день | **$90 000** |

> Ключ к росту — количество пользователей в боте.
> STON.fi уже делает 27 млн свапов всего — твоя задача привести аудиторию.
