#!/usr/bin/env bash
# Сторож бота. Запускается по cron раз в минуту (ставит deploy/always-on.sh).
# Что делает:
#   1) процесс умер / не запущен  → поднимает;
#   2) процесс жив, но завис (не обновляет data/heartbeat) → перезапускает;
#   3) несколько копий бота одновременно → убивает лишние (по PID);
#   4) лог вырос больше 50 МБ → подрезает, чтобы не забить диск.
#
# Ручной запуск: bash deploy/watchdog.sh
set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$DIR/data/bot.pid"
BEAT="$DIR/data/heartbeat"
LOGFILE="$DIR/data/bot.log"
WLOG="$DIR/data/watchdog.log"
LOCK="$DIR/data/watchdog.lock"
mkdir -p "$DIR/data"

STAMP="$(date '+%Y-%m-%d %H:%M:%S')"
say() { printf '%s %s\n' "$STAMP" "$*" >>"$WLOG"; }

# один сторож за раз
if command -v flock >/dev/null 2>&1; then
    exec 8>"$LOCK"
    flock -n 8 || exit 0
fi

# ---------- ротация лога ----------
if [ -f "$LOGFILE" ]; then
    SIZE=$(wc -c <"$LOGFILE" 2>/dev/null || echo 0)
    if [ "${SIZE:-0}" -gt 52428800 ]; then
        tail -n 3000 "$LOGFILE" >"$LOGFILE.tmp" 2>/dev/null && mv "$LOGFILE.tmp" "$LOGFILE"
        say "лог был больше 50 МБ — подрезал до последних 3000 строк"
    fi
fi

# ---------- сколько сейчас копий бота ----------
mapfile -t BOT_PIDS < <(
    ps -eo pid=,args= 2>/dev/null \
    | awk '/python/ && /bot\.py/ && !/--selftest/ && !/--menu/ && !/--version/ {print $1}' \
    | sort -u
)

# копии из другой папки — это тот же бот, но запущенный не отсюда.
# Смотрим на структуру проекта (bot.py + app/config.py), чтобы не тронуть чужие боты.
OTHER=()
DIR_REAL="$(readlink -f "$DIR")"
for pid in "${BOT_PIDS[@]:-}"; do
    [ -z "${pid:-}" ] && continue
    cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
    [ -z "$cwd" ] && continue
    [ "$cwd" = "$DIR_REAL" ] && continue
    if [ -f "$cwd/bot.py" ] && { [ -f "$cwd/app/config.py" ] || [ -f "$cwd/app/logic.py" ]; }; then
        OTHER+=("$pid:$cwd")
    fi
done

kill_pid() {
    local pid="$1" i
    kill -0 "$pid" 2>/dev/null || return 0
    kill "$pid" 2>/dev/null || true
    for i in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || return 0
        sleep 0.5
    done
    kill -9 "$pid" 2>/dev/null || true
}

# ---------- режим systemd ----------
HAS_SYSTEMD=0
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q '^mc-bot\.service'; then
    HAS_SYSTEMD=1
fi

# зависание: heartbeat не обновлялся дольше 5 минут
STUCK=0
if [ -f "$BEAT" ]; then
    BEAT_TS=$(stat -c %Y "$BEAT" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    if [ "${BEAT_TS:-0}" -gt 0 ] && [ $((NOW - BEAT_TS)) -gt 300 ]; then
        STUCK=1
    fi
fi

if [ "$HAS_SYSTEMD" = "1" ]; then
    if ! systemctl is-active --quiet mc-bot.service; then
        say "systemd: бот не активен — запускаю"
        systemctl reset-failed mc-bot.service 2>/dev/null || true
        systemctl start mc-bot.service 2>/dev/null || true
    elif [ "$STUCK" = "1" ]; then
        say "systemd: процесс жив, но завис (нет heartbeat > 5 мин) — перезапускаю"
        systemctl restart mc-bot.service 2>/dev/null || true
    fi
    # лишние копии из других папок (из-за них Telegram отдаёт апдейты не тому процессу)
    for item in "${OTHER[@]:-}"; do
        [ -z "${item:-}" ] && continue
        pid="${item%%:*}"
        kill_pid "$pid"
        say "убил лишнюю копию из другой папки: PID $pid (${item#*:})"
    done
    exit 0
fi

# ---------- режим без systemd (pid-файл) ----------
ALIVE=0
if [ -f "$PIDFILE" ]; then
    OLD="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
        ALIVE=1
    fi
fi

if [ "$ALIVE" = "1" ] && [ "$STUCK" = "1" ]; then
    say "бот жив, но завис (нет heartbeat > 5 мин) — перезапускаю"
    kill_pid "$(cat "$PIDFILE")"
    rm -f "$PIDFILE"
    ALIVE=0
fi

# гасим лишние копии, иначе будет конфликт 409
for item in "${OTHER[@]:-}"; do
    [ -z "${item:-}" ] && continue
    pid="${item%%:*}"
    kill_pid "$pid"
    say "убил лишнюю копию: PID $pid (${item#*:})"
done

if [ "$ALIVE" = "1" ]; then
    exit 0
fi

say "бот не работает — поднимаю"
bash "$DIR/deploy/start-bot.sh" >>"$WLOG" 2>&1 8>&- || say "старт не удался, смотри $LOGFILE"
