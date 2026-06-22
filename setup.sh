#!/usr/bin/env bash
# =============================================================================
# setup.sh — Полностью автоматическая установка STON.fi Omniston Telegram бота
#
# Нужен ТОЛЬКО один шаг вручную:
#   → Получить токен бота в @BotFather (30 секунд)
#
# Всё остальное делается автоматически:
#   ✓ Python окружение
#   ✓ Зависимости
#   ✓ TON кошелёк бота
#   ✓ Адрес для комиссий
#   ✓ Конфигурация .env
#   ✓ Запуск
#
# Использование:
#   chmod +x setup.sh && ./setup.sh
# =============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
RED='\033[0;31m'
NC='\033[0m'

print_header() {
    clear
    echo ""
    echo -e "${BOLD}${BLUE}╔══════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${BLUE}║   STON.fi Omniston Bot — Автоустановка  ║${NC}"
    echo -e "${BOLD}${BLUE}╚══════════════════════════════════════════╝${NC}"
    echo ""
}

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
info() { echo -e "  ${BLUE}→${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
step() { echo -e "\n${BOLD}$1${NC}"; }

# =============================================================================
# Проверка Python
# =============================================================================
find_python() {
    for cmd in python3.12 python3.11 python3.10 python3 python; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [ "${major:-0}" -ge 3 ] && [ "${minor:-0}" -ge 10 ]; then
                echo "$cmd"
                return
            fi
        fi
    done
    echo ""
}

# =============================================================================
# Основной сценарий
# =============================================================================
main() {
    print_header

    # ── 1. Python ────────────────────────────────────────────────────────────
    step "1/5  Проверка Python"
    PYTHON=$(find_python)
    if [ -z "$PYTHON" ]; then
        echo -e "  ${RED}✗ Python 3.10+ не найден.${NC}"
        info "Установи: https://www.python.org/downloads/"
        exit 1
    fi
    ok "$("$PYTHON" --version)"

    # ── 2. Виртуальное окружение ─────────────────────────────────────────────
    step "2/5  Виртуальное окружение"
    if [ ! -d ".venv" ]; then
        "$PYTHON" -m venv .venv
        ok "Создано .venv"
    else
        ok ".venv уже есть"
    fi
    # shellcheck disable=SC1091
    source .venv/bin/activate

    # ── 3. Зависимости ───────────────────────────────────────────────────────
    step "3/5  Установка зависимостей"
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
    ok "Все пакеты установлены"

    # ── 4. Telegram Bot Token ────────────────────────────────────────────────
    step "4/5  Telegram Bot Token"

    # Если токен уже есть в .env — не спрашиваем
    EXISTING_TOKEN=""
    if [ -f ".env" ]; then
        EXISTING_TOKEN=$(grep -E "^TELEGRAM_BOT_TOKEN=.+" .env 2>/dev/null | cut -d= -f2- || true)
    fi

    if [ -n "$EXISTING_TOKEN" ]; then
        ok "Токен уже сохранён в .env"
        BOT_TOKEN="$EXISTING_TOKEN"
    else
        echo ""
        echo -e "  ${BOLD}Как получить токен (30 секунд):${NC}"
        echo "  ┌─────────────────────────────────────────┐"
        echo "  │  1. Открой Telegram                     │"
        echo "  │  2. Найди @BotFather                    │"
        echo "  │  3. Отправь команду /newbot             │"
        echo "  │  4. Введи любое имя бота                │"
        echo "  │  5. Введи username (например mybot_bot) │"
        echo "  │  6. Скопируй токен вида:                │"
        echo "  │     1234567890:AAxxxxxxxxxxxxxxxxxxxxxx │"
        echo "  └─────────────────────────────────────────┘"
        echo ""
        echo -ne "  ${BOLD}Вставь токен сюда:${NC} "
        read -r BOT_TOKEN

        while [ -z "$BOT_TOKEN" ]; do
            warn "Токен не может быть пустым."
            echo -ne "  ${BOLD}Вставь токен:${NC} "
            read -r BOT_TOKEN
        done

        # Создаём .env только с токеном — остальное добавится автоматически
        cat > .env << EOF
TELEGRAM_BOT_TOKEN=${BOT_TOKEN}
REFERRER_FEE_BPS=30
SLIPPAGE_BPS=100
USE_SANDBOX=false
EOF
        ok ".env создан"
        warn "Больше ничего вводить не нужно — остальное настроится автоматически"
    fi

    # ── 5. Папка для данных ──────────────────────────────────────────────────
    step "5/5  Подготовка"
    mkdir -p data
    ok "Папка data/ готова"

    # ── Запуск ───────────────────────────────────────────────────────────────
    echo ""
    echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${GREEN}║         Установка завершена!             ║${NC}"
    echo -e "${BOLD}${GREEN}╠══════════════════════════════════════════╣${NC}"
    echo -e "${BOLD}${GREEN}║  При первом запуске бот:                 ║${NC}"
    echo -e "${BOLD}${GREEN}║  • Создаст TON кошелёк автоматически     ║${NC}"
    echo -e "${BOLD}${GREEN}║  • Покажет адрес для пополнения          ║${NC}"
    echo -e "${BOLD}${GREEN}║  • Начнёт собирать комиссии 0.3%         ║${NC}"
    echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  Следующие запуски:"
    echo -e "  ${BOLD}source .venv/bin/activate && python telegram_bot.py${NC}"
    echo ""
    echo -ne "  Запустить бота прямо сейчас? (Y/n): "
    read -r run_now

    if [[ ! "${run_now:-y}" =~ ^[Nn]$ ]]; then
        echo ""
        echo -e "${YELLOW}  Остановить: Ctrl+C${NC}"
        echo ""
        python telegram_bot.py
    else
        info "Запусти вручную: python telegram_bot.py"
    fi
}

# Проверяем что запущен из папки проекта
if [ ! -f "telegram_bot.py" ]; then
    echo -e "${RED}Запусти скрипт из папки проекта (где находится telegram_bot.py).${NC}"
    exit 1
fi

main "$@"
