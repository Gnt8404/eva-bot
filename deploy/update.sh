#!/usr/bin/env bash
# Обновление бота и обязательный перезапуск после обновления.
#   bash deploy/update.sh [папка-с-новой-версией]
#
# Кладём новый код, сохраняем базу и логи, прогоняем самопроверку и поднимаем бота.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:-}"
STAMP="$(date '+%Y%m%d-%H%M%S')"

cd "$DIR"
mkdir -p data backups

echo "[1/5] бэкап базы и настроек"
if [ -f data/bot.db ]; then
    cp data/bot.db "backups/bot-$STAMP.db"
    echo "     сохранено: backups/bot-$STAMP.db"
else
    echo "     базы пока нет — пропускаю"
fi

if [ -n "$SRC" ]; then
    echo "[2/5] копирую новую версию из $SRC"
    for item in app knowledge bot.py requirements.txt .env .env.example Dockerfile Procfile; do
        if [ -e "$SRC/$item" ]; then
            rm -rf "$DIR/$item.old"
            [ -e "$DIR/$item" ] && mv "$DIR/$item" "$DIR/$item.old"
            cp -a "$SRC/$item" "$DIR/$item"
            rm -rf "$DIR/$item.old"
        fi
    done
else
    echo "[2/5] папка с новой версией не указана — обновляю только перезапуском"
fi

echo "[3/5] самопроверка"
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
    echo "     Python не найден — пропускаю самопроверку"
elif "$PY" bot.py --selftest; then
    echo "     проверка пройдена"
else
    echo "     проверка с ошибками — бот всё равно поднимается с Restart=always,"
    echo "     но лог стоит посмотреть: data/bot.log"
fi

echo "[4/5] перезапуск — бот обязан быть включён после обновления"
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q '^mc-bot.service'; then
    systemctl restart mc-bot.service
    sleep 2
    systemctl is-active mc-bot.service && echo "     systemd: бот работает" || echo "     systemd: что-то не так, смотри journalctl -u mc-bot"
else
    bash deploy/stop-bot.sh || true
    bash deploy/start-bot.sh
fi

echo "[5/5] готово. Последние строки лога:"
tail -n 10 data/bot.log 2>/dev/null || true
