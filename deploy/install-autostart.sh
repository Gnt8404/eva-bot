#!/usr/bin/env bash
# Автозапуск бота: systemd, а если его нет — cron-сторож.
#   sudo bash deploy/install-autostart.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"

if command -v systemctl >/dev/null 2>&1 && [ -d /etc/systemd/system ]; then
    echo "[1/3] ставлю systemd-юнит (папка бота: $DIR)"
    sed "s|__BOT_DIR__|$DIR|g" "$DIR/deploy/mc-bot.service" > /tmp/mc-bot.service
    install -m 644 /tmp/mc-bot.service /etc/systemd/system/mc-bot.service
    rm -f /tmp/mc-bot.service
    echo "[2/3] включаю автозапуск"
    systemctl daemon-reload
    systemctl enable mc-bot.service
    systemctl restart mc-bot.service
    sleep 2
    echo "[3/3] состояние:"
    systemctl is-active mc-bot.service || true
    echo "Логи: journalctl -u mc-bot -f   (или $DIR/data/bot.log)"
    exit 0
fi

echo "[1/2] systemd не найден — ставлю cron-сторожа (каждые 2 минуты)"
CRON_LINE="*/2 * * * * bash $DIR/deploy/watchdog.sh >> $DIR/data/watchdog.log 2>&1"
mkdir -p "$DIR/data"
( crontab -l 2>/dev/null | grep -v 'watchdog.sh' ; echo "$CRON_LINE" ) | crontab -
echo "[2/2] готово. Текущее расписание:"
crontab -l | tail -n 3
bash "$DIR/deploy/start-bot.sh"
