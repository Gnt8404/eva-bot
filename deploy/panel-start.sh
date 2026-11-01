#!/usr/bin/env bash
# ЗАПУСК ДЛЯ ПАНЕЛИ ХОСТИНГА (Pterodactyl, Pelican, Bot-Hosting и любая панель,
# где в поле «команда запуска» нужна одна команда).
#
#   bash deploy/panel-start.sh
#
# Скрипт держит бота включённым: если процесс упал или его убило — поднимает
# заново через 3 секунды. Выход из цикла — только по Ctrl+C или остановке панели.
set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR" || exit 1
mkdir -p data

# находим python (в панелях бывает python3 или python)
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
    echo "Python не найден. Установи python3 или укажи путь в PY=..."
    exit 1
fi

echo "[$(date '+%F %T')] Ева: запуск из $DIR"
while true; do
    "$PY" -u bot.py
    CODE=$?
    if [ "$CODE" = "0" ]; then
        echo "[$(date '+%F %T')] бот завершился сам (код 0). Перезапуск через 3 сек"
    else
        echo "[$(date '+%F %T')] бот остановился (код $CODE). Перезапуск через 3 сек"
    fi
    sleep 3
done
