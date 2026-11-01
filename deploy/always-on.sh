#!/usr/bin/env bash
# ПОСТОЯННАЯ РАБОТА БОТА: автозапуск при загрузке + автоперезапуск при падении.
#
#   sudo bash deploy/always-on.sh              # поставить/починить (папка определяется сама)
#   sudo bash deploy/always-on.sh /путь/к/боту # указать папку вручную
#   bash deploy/always-on.sh --status          # проверить, что сейчас с ботом
#
# Что делает:
#   1. находит все запущенные копии бота и гасит лишние (по PID, не «всё подряд»);
#   2. ставит systemd-юнит mc-bot.service с Restart=always (если есть права root);
#   3. дополнительно ставит cron-сторожа раз в минуту — он поднимает бота,
#      если процесс завис или юнит кто-то остановил;
#   4. включает ротацию логов, чтобы диск не забился;
#   5. запускает бота и печатает отчёт.
#
# Без root тоже работает: тогда вместо systemd используется cron (@reboot + раз в минуту).
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ARG="${1:-}"

say()  { printf '%s\n' "$*"; }
hr()   { say "------------------------------------------------------------"; }
log()  { say "   $*"; }

# ---------------------------------------------------------------- статус
show_status() {
    local dir="$1"
    hr
    say "СОСТОЯНИЕ БОТА ($dir)"
    hr
    if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q '^mc-bot\.service'; then
        if systemctl is-active --quiet mc-bot.service; then
            say "systemd: РАБОТАЕТ (автозапуск: $(systemctl is-enabled mc-bot.service 2>/dev/null || echo '?'))"
        else
            say "systemd: НЕ РАБОТАЕТ"
        fi
    else
        say "systemd: юнит не установлен (режим cron)"
    fi
    say "cron-сторож: $( (crontab -l 2>/dev/null || true) | grep -c 'watchdog.sh' ) строк(и)"
    if [ -f "$dir/data/heartbeat" ]; then
        local age=$(( $(date +%s) - $(stat -c %Y "$dir/data/heartbeat" 2>/dev/null || echo 0) ))
        say "heartbeat: ${age} сек назад"
    else
        say "heartbeat: файла пока нет"
    fi
    say "процессы бота:"
    ps -eo pid=,etime=,args= 2>/dev/null | awk '/python/ && /bot\.py/ && !/--selftest/ && !/--menu/ && !/--version/ {print "   PID " $1 "  работает " $2}'
    if [ -f "$dir/data/bot.log" ]; then
        say "последние строки лога:"
        tail -n 8 "$dir/data/bot.log" | sed 's/^/   /'
    fi
    hr
}

# -------------------------------------------------- папка с ботом
if [ "$ARG" = "--status" ]; then
    show_status "$SELF_DIR"
    exit 0
fi

if [ -n "$ARG" ] && [ "${ARG#-}" = "$ARG" ]; then
    if [ -d "$ARG" ] && [ -f "$ARG/bot.py" ]; then
        DIR="$(cd "$ARG" && pwd)"
    else
        say "Папка не найдена или в ней нет bot.py: $ARG"
        exit 1
    fi
else
    DIR="$SELF_DIR"
fi

if [ ! -f "$DIR/bot.py" ]; then
    say "В папке $DIR нет bot.py — нечего запускать."
    exit 1
fi

IS_ROOT=0
[ "$(id -u)" = "0" ] && IS_ROOT=1

hr
say "1. Папка бота: $DIR"
say "   права: $( [ "$IS_ROOT" = 1 ] && echo root || echo "обычный пользователь ($(id -un))" )"

# -------------------------------------------------- гасим лишние копии
hr
say "2. Проверяю, не крутится ли где-то ещё одна копия"
KILLED=0
while read -r pid args; do
    [ -z "${pid:-}" ] && continue
    case "$args" in
        *bot.py*--selftest*|*bot.py*--menu*|*bot.py*--version*) continue ;;
    esac
    cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
    [ -z "$cwd" ] && continue
    DIR_REAL="$(readlink -f "$DIR")"
    # своя копия — её перезапустим штатно ниже
    [ "$cwd" = "$DIR_REAL" ] && continue
    # старая копия «Евы» из другой папки: та же структура проекта.
    # Чужие боты на сервере не трогаем.
    if [ -f "$cwd/bot.py" ] && { [ -f "$cwd/app/config.py" ] || [ -f "$cwd/app/logic.py" ]; }; then
        say "   останавливаю старую копию PID $pid (папка $cwd)"
        kill "$pid" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.5
        done
        kill -9 "$pid" 2>/dev/null || true
        KILLED=$((KILLED+1))
    else
        say "   пропускаю посторонний процесс PID $pid ($cwd)"
    fi
done < <(ps -eo pid=,args= 2>/dev/null | awk '/python/ && /bot\.py/ {print $1, $0}' | sort -u)
[ "$KILLED" = "0" ] && say "   старых копий из других папок нет"
mkdir -p "$DIR/data"

# -------------------------------------------------- systemd
USE_SYSTEMD=0
if [ "$IS_ROOT" = "1" ] && command -v systemctl >/dev/null 2>&1 && [ -d /etc/systemd/system ]; then
    USE_SYSTEMD=1
fi

if [ "$USE_SYSTEMD" = "1" ]; then
    hr
    say "3. Ставлю systemd-юнит mc-bot.service (Restart=always)"
    sed "s|__BOT_DIR__|$DIR|g" "$DIR/deploy/mc-bot.service" > /etc/systemd/system/mc-bot.service
    chmod 644 /etc/systemd/system/mc-bot.service
    systemctl daemon-reload
    systemctl enable mc-bot.service >/dev/null 2>&1
    systemctl reset-failed mc-bot.service 2>/dev/null || true
    systemctl restart mc-bot.service
    sleep 3
    if systemctl is-active --quiet mc-bot.service; then
        log "юнит активен, автозапуск включён"
    else
        log "юнит не поднялся, смотри: journalctl -u mc-bot -n 50"
    fi
else
    hr
    say "3. systemd недоступен (нет root или системы) — работаю через cron + nohup"
    if [ "$IS_ROOT" = "0" ]; then
        log "подсказка: запусти с sudo, тогда получишь systemd-автозапуск (надёжнее)"
    fi
fi

# -------------------------------------------------- логротация
if [ "$IS_ROOT" = "1" ] && [ -d /etc/logrotate.d ]; then
    hr
    say "4. Ротация логов (/etc/logrotate.d/mc-bot)"
    sed "s|__BOT_DIR__|$DIR|g" "$DIR/deploy/mc-bot.logrotate" > /etc/logrotate.d/mc-bot
    chmod 644 /etc/logrotate.d/mc-bot
    log "готова: лог не разрастётся и не забьёт диск"
else
    hr
    say "4. Ротация логов: пропускаю (нужен root) — сторож всё равно подрежет лог больше 50 МБ"
fi

# -------------------------------------------------- cron-сторож (второй слой)
hr
say "5. Ставлю cron-сторожа (раз в минуту + при загрузке сервера)"
mkdir -p "$DIR/data"
CRON_TMP="$(mktemp)"
( crontab -l 2>/dev/null || true ) | grep -v 'deploy/watchdog.sh' | grep -v 'deploy/always-on.sh' > "$CRON_TMP"
{
    echo "* * * * * bash $DIR/deploy/watchdog.sh >> $DIR/data/watchdog.log 2>&1"
    if [ "$USE_SYSTEMD" = "0" ]; then
        echo "@reboot sleep 20 && bash $DIR/deploy/start-bot.sh >> $DIR/data/watchdog.log 2>&1"
    fi
} >> "$CRON_TMP"
if crontab "$CRON_TMP" 2>/dev/null; then
    log "cron установлен"
else
    log "cron недоступен — сторож не поставлен. Запусти скрипт через sudo, тогда будет systemd + сторож"
fi
rm -f "$CRON_TMP"

# -------------------------------------------------- запуск и проверка
if [ "$USE_SYSTEMD" = "0" ]; then
    hr
    say "6. Перезапускаю бота на актуальном коде"
    bash "$DIR/deploy/stop-bot.sh" >/dev/null 2>&1 || true
    bash "$DIR/deploy/start-bot.sh"
fi

hr
say "7. Проверка"
sleep 5
RUNNING=0
while read -r pid args; do
    [ -z "${pid:-}" ] && continue
    case "$args" in *bot.py*--selftest*|*bot.py*--menu*|*bot.py*--version*) continue ;; esac
    RUNNING=$((RUNNING+1))
    log "работает PID $pid"
done < <(ps -eo pid=,args= 2>/dev/null | awk '/python/ && /bot\.py/ {print $1, $0}' | sort -u)

if [ "$RUNNING" = "0" ]; then
    say "   БОТ НЕ ПОДНЯЛСЯ. Последние строки лога:"
    tail -n 20 "$DIR/data/bot.log" 2>/dev/null | sed 's/^/     /'
elif [ "$RUNNING" -gt 1 ]; then
    say "   ВНИМАНИЕ: копий больше одной ($RUNNING) — это ломает приём сообщений."
    say "   Запусти скрипт ещё раз, он оставит одну."
else
    say "   бот работает, копия одна — всё как надо"
fi

hr
say "ГОТОВО. Что теперь происходит само:"
say "   • сервер перезагрузился  → бот стартует вместе с системой"
say "   • бот упал / убит        → поднимается через 3 секунды"
say "   • бот завис              → сторож перезапускает в течение минуты"
say ""
say "Проверить в любой момент:  bash deploy/always-on.sh --status"
say "Живой лог:                 tail -f $DIR/data/bot.log"
[ "$USE_SYSTEMD" = "1" ] && say "Лог systemd:               journalctl -u mc-bot -f"
hr
