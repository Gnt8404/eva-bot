#!/usr/bin/env bash
# Остановка бота (по своему pid-файлу, без убийства чужих процессов).
#   bash deploy/stop-bot.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$DIR/data/bot.pid"

if [ ! -f "$PIDFILE" ]; then
    echo "PID-файл не найден — бот, похоже, не запущен через start-bot.sh."
    exit 0
fi

PID="$(cat "$PIDFILE")"
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$PID" 2>/dev/null || break
        sleep 0.5
    done
    if kill -0 "$PID" 2>/dev/null; then
        kill -9 "$PID"
    fi
    echo "Бот остановлен (PID $PID)."
else
    echo "Процесс $PID уже не жив."
fi
rm -f "$PIDFILE"
