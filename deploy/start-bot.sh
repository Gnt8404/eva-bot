#!/usr/bin/env bash
# Запуск бота в фоне — с защитой от двойного запуска.
#   bash deploy/start-bot.sh
# Два экземпляра одновременно = Telegram отдаёт апдейты только одному (409),
# поэтому второй запуск блокируется файловой блокировкой.
set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$DIR/data/bot.pid"
LOGFILE="$DIR/data/bot.log"
LOCKFILE="$DIR/data/start.lock"
mkdir -p "$DIR/data"

# уже работает по pid-файлу?
if [ -f "$PIDFILE" ]; then
    OLD="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
        echo "Бот уже работает, PID $OLD."
        exit 0
    fi
    rm -f "$PIDFILE"
fi

# берём блокировку, чтобы два старта не полезли одновременно
if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCKFILE"
    if ! flock -n 9; then
        echo "Другой запуск уже идёт — выхожу."
        exit 0
    fi
fi

cd "$DIR" || exit 1
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
    echo "Python не найден. Установи python3."
    exit 1
fi
nohup "$PY" -u bot.py >>"$LOGFILE" 2>&1 9>&- &
NEWPID=$!
echo "$NEWPID" > "$PIDFILE"
sleep 2

if kill -0 "$NEWPID" 2>/dev/null; then
    echo "Бот запущен, PID $NEWPID. Лог: $LOGFILE"
    exit 0
fi

echo "Бот не поднялся. Последние строки лога:"
tail -n 20 "$LOGFILE" 2>/dev/null || true
rm -f "$PIDFILE"
exit 1
