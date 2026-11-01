#!/usr/bin/env bash
# Что сейчас с ботом: процесс, последние строки лога, статистика базы.
#   bash deploy/status-bot.sh
set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$DIR/data/bot.pid"

echo "== процесс =="
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q '^mc-bot.service'; then
    systemctl is-active mc-bot.service && echo "systemd: mc-bot.service работает" || echo "systemd: mc-bot.service не работает"
elif [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "бот работает, PID $(cat "$PIDFILE")"
else
    echo "бот не запущен"
fi

echo
echo "== защита (автоперезапуск) =="
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q '^mc-bot\.service'; then
    echo "systemd: $(systemctl is-active mc-bot.service 2>/dev/null) / автозапуск: $(systemctl is-enabled mc-bot.service 2>/dev/null)"
else
    echo "systemd: юнит не установлен"
fi
echo "cron-сторож: $( (crontab -l 2>/dev/null || true) | grep -c 'deploy/watchdog.sh' ) строк(и)"
if [ -f "$DIR/data/heartbeat" ]; then
    echo "heartbeat: $(( $(date +%s) - $(stat -c %Y "$DIR/data/heartbeat" 2>/dev/null || echo 0) )) сек назад (норма — меньше 300)"
else
    echo "heartbeat: файла нет — бот ещё не дошёл до приёма сообщений"
fi
echo "процессов бота: $(ps -eo args= 2>/dev/null | awk '/python/ && /bot\.py/ && !/--selftest/ && !/--menu/ && !/--version/' | wc -l) (должен быть 1)"

echo
echo "== последние строки лога =="
tail -n 12 "$DIR/data/bot.log" 2>/dev/null || echo "лога пока нет"

echo
echo "== база бота =="
if [ -f "$DIR/data/bot.db" ]; then
    python3 - "$DIR/data/bot.db" <<'PY2'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.row_factory = sqlite3.Row
try:
    users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    violations = conn.execute("SELECT COUNT(*) c FROM violations").fetchone()["c"]
    tickets = conn.execute("SELECT COUNT(*) c FROM tickets").fetchone()["c"]
    print("пользователей: %d, обращений: %d, нарушений: %d" % (users, tickets, violations))
    rows = conn.execute("SELECT username,user_id,word,created_at FROM violations "
                        "ORDER BY id DESC LIMIT 5").fetchall()
    for r in rows:
        name = ("@" + r["username"]) if r["username"] else str(r["user_id"])
        print("  %s — %s" % (name, r["word"]))
except sqlite3.Error as exc:
    print("база пустая или ещё не создана: %s" % exc)
PY2
else
    echo "базы пока нет — бот ещё не создавал данные"
fi
