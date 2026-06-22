#!/usr/bin/env bash
# =============================================================================
# setup.sh — Автоматическая установка и запуск STON.fi Omniston Telegram бота
#
# Что делает этот скрипт:
#   1. Проверяет Python 3.10+
#   2. Создаёт виртуальное окружение .venv
#   3. Устанавливает все зависимости
#   4. Интерактивно собирает настройки и создаёт .env
#   5. Создаёт папку data/ для хранения кошелька
#   6. Генерирует TON кошелёк бота (если нет)
#   7. Запускает бота
#
# Использование:
#   chmod +x setup.sh
#   ./setup.sh
# =============================================================================

set -euo pipefail

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Версия Python
PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=10

# =============================================================================
# Вспомогательные функции
# =============================================================================

print_header() {
    echo ""
    echo -e "${BOLD}${BLUE}============================================${NC}"
    echo -e "${BOLD}${BLUE}  STON.fi Omniston Bot — Автоустановка${NC}"
    echo -e "${BOLD}${BLUE}============================================${NC}"
    echo ""
}

print_step() {
    echo -e "\n${BOLD}${GREEN}▶ $1${NC}"
}

print_info() {
    echo -e "  ${BLUE}ℹ $1${NC}"
}

print_warn() {
    echo -e "  ${YELLOW}⚠ $1${NC}"
}

print_error() {
    echo -e "  ${RED}✗ $1${NC}"
}

print_ok() {
    echo -e "  ${GREEN}✓ $1${NC}"
}

ask() {
    # ask VAR_NAME "Prompt text" "default_value"
    local var_name="$1"
    local prompt="$2"
    local default="${3:-}"
    local input

    if [ -n "$default" ]; then
        echo -ne "  ${BOLD}$prompt${NC} [${default}]: "
    else
        echo -ne "  ${BOLD}$prompt${NC}: "
    fi

    read -r input
    if [ -z "$input" ] && [ -n "$default" ]; then
        input="$default"
    fi
    # Export to caller via eval
    eval "$var_name=\"$input\""
}

ask_secret() {
    # ask_secret VAR_NAME "Prompt text"
    local var_name="$1"
    local prompt="$2"
    local input

    echo -ne "  ${BOLD}$prompt${NC} (не отображается): "
    read -rs input
    echo ""
    eval "$var_name=\"$input\""
}

# =============================================================================
# Шаг 1: Проверка Python
# =============================================================================

check_python() {
    print_step "Проверка Python"

    local python_cmd=""
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            local version
            version=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
            local major minor
            major=$(echo "$version" | cut -d. -f1)
            minor=$(echo "$version" | cut -d. -f2)
            if [ "$major" -ge "$PYTHON_MIN_MAJOR" ] && [ "$minor" -ge "$PYTHON_MIN_MINOR" ]; then
                python_cmd="$cmd"
                print_ok "Найден $cmd $("$cmd" --version 2>&1)"
                break
            fi
        fi
    done

    if [ -z "$python_cmd" ]; then
        print_error "Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+ не найден."
        print_info "Установи Python: https://www.python.org/downloads/"
        exit 1
    fi

    echo "$python_cmd"
}

# =============================================================================
# Шаг 2: Виртуальное окружение
# =============================================================================

setup_venv() {
    local python_cmd="$1"
    print_step "Настройка виртуального окружения"

    if [ -d ".venv" ]; then
        print_ok "Окружение .venv уже существует"
    else
        "$python_cmd" -m venv .venv
        print_ok "Создано окружение .venv"
    fi

    # Activate
    # shellcheck disable=SC1091
    source .venv/bin/activate
    print_ok "Окружение активировано"
}

# =============================================================================
# Шаг 3: Зависимости
# =============================================================================

install_deps() {
    print_step "Установка зависимостей"

    if [ ! -f "requirements.txt" ]; then
        print_error "requirements.txt не найден. Запусти скрипт из папки проекта."
        exit 1
    fi

    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
    print_ok "Все зависимости установлены"
}

# =============================================================================
# Шаг 4: Конфигурация .env
# =============================================================================

setup_env() {
    print_step "Настройка конфигурации"

    if [ -f ".env" ]; then
        print_warn ".env уже существует."
        echo -ne "  Перезаписать? (y/N): "
        read -r overwrite
        if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
            print_ok ".env оставлен без изменений"
            return
        fi
    fi

    echo ""
    print_info "Нужно 3 обязательных значения. Получи их:"
    print_info "1. TELEGRAM_BOT_TOKEN  → @BotFather в Telegram → /newbot"
    print_info "2. REFERRER_TON_ADDRESS → @wallet в Telegram → твой TON адрес"
    print_info "3. TONCENTER_API_KEY   → @toncenter в Telegram (можно пропустить)"
    echo ""

    local bot_token referrer_address toncenter_key fee_bps slippage sandbox

    ask_secret bot_token "Telegram Bot Token (@BotFather)"
    while [ -z "$bot_token" ]; do
        print_error "Токен обязателен."
        ask_secret bot_token "Telegram Bot Token"
    done

    ask referrer_address "Твой TON адрес для комиссий (EQ...)" ""
    while [ -z "$referrer_address" ]; do
        print_error "TON адрес обязателен."
        ask referrer_address "Твой TON адрес (EQ...)" ""
    done

    ask toncenter_key "Toncenter API Key (Enter чтобы пропустить)" ""
    ask fee_bps "Комиссия в basis points" "30"
    ask slippage "Макс. проскальзывание в basis points" "100"
    ask sandbox "Тестовый режим (testnet)? (true/false)" "false"

    cat > .env << EOF
# Автоматически создан setup.sh
TELEGRAM_BOT_TOKEN=${bot_token}
REFERRER_TON_ADDRESS=${referrer_address}
TONCENTER_API_KEY=${toncenter_key}
REFERRER_FEE_BPS=${fee_bps}
SLIPPAGE_BPS=${slippage}
USE_SANDBOX=${sandbox}
EOF

    print_ok ".env создан"
    print_warn "ВАЖНО: никогда не добавляй .env в git — он уже в .gitignore"
}

# =============================================================================
# Шаг 5: Создание папки data/
# =============================================================================

setup_data_dir() {
    print_step "Создание папки для данных"
    mkdir -p data
    print_ok "Папка data/ готова (кошелёк бота будет сохранён туда)"
}

# =============================================================================
# Шаг 6: Инициализация кошелька (создаётся при первом запуске бота)
# =============================================================================

init_wallet() {
    print_step "Инициализация TON кошелька бота"

    if [ -f "data/wallet.json" ]; then
        print_ok "Кошелёк уже существует: data/wallet.json"
        local address
        address=$(python3 -c "import json; d=json.load(open('data/wallet.json')); print(d['address_bounceable'])" 2>/dev/null || echo "неизвестен")
        print_info "Адрес кошелька: $address"
    else
        print_info "Кошелёк будет создан при первом запуске бота."
        print_info "Мнемоника (24 слова) будет показана в терминале — СОХРАНИ ЕЁ!"
    fi
}

# =============================================================================
# Шаг 7: Запуск бота
# =============================================================================

start_bot() {
    print_step "Запуск бота"
    echo ""
    echo -e "${BOLD}${GREEN}Всё готово! Запускаю бота...${NC}"
    echo -e "${YELLOW}Остановить: Ctrl+C${NC}"
    echo ""

    # shellcheck disable=SC1091
    source .venv/bin/activate
    python telegram_bot.py
}

# =============================================================================
# Главная функция
# =============================================================================

main() {
    print_header

    # Проверяем что мы в правильной папке
    if [ ! -f "telegram_bot.py" ]; then
        print_error "Запусти скрипт из папки проекта (там где telegram_bot.py)."
        exit 1
    fi

    local python_cmd
    python_cmd=$(check_python)

    setup_venv "$python_cmd"
    install_deps
    setup_env
    setup_data_dir
    init_wallet

    echo ""
    echo -e "${BOLD}${GREEN}============================================${NC}"
    echo -e "${BOLD}${GREEN}  Установка завершена!${NC}"
    echo -e "${BOLD}${GREEN}============================================${NC}"
    echo ""
    echo -e "  Следующий раз запускай бота так:"
    echo -e "  ${BOLD}source .venv/bin/activate && python telegram_bot.py${NC}"
    echo ""

    echo -ne "  Запустить бота сейчас? (Y/n): "
    read -r run_now
    if [[ ! "$run_now" =~ ^[Nn]$ ]]; then
        start_bot
    else
        print_info "Для запуска используй: python telegram_bot.py"
    fi
}

main "$@"
