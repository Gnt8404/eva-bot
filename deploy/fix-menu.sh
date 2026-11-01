#!/usr/bin/env bash
# Жёсткое обновление бота: находит ту папку, из которой бот РЕАЛЬНО запущен,
# кладёт туда новый код, убивает все старые копии процесса и поднимает бота заново.
#
#   bash deploy/fix-menu.sh                 # обновить папку, из которой крутится бот
#   bash deploy/fix-menu.sh /путь/к/боту    # обновить конкретную папку
#
# Зачем: если раньше бот обновлялся в одной папке, а запущен из другой
# (или в фоне висела старая копия), кнопки в Телеграме остаются старыми.
set -uo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
ARG="${1:-}"
STAMP="$(date '+%Y%m%d-%H%M%S')"

say() { printf '%s\n' "$*"; }
hr()  { say "------------------------------------------------------------"; }

hr
say "1. Ищу запущенные копии бота"
# берём только python-процессы с bot.py в аргументах (сам скрипт сюда не попадает)
PIDS=()
DIRS=()
while read -r pid args; do
    [ -z "${pid:-}" ] && continue
    case "$args" in
        *bot.py*--selftest*|*bot.py*--menu*|*bot.py*--version*) continue ;;
    esac
    PIDS+=("$pid")
    cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
    script="$(printf '%s' "$args" | tr ' ' '\n' | grep -m1 'bot\.py$' || true)"
    if [ -n "$script" ] && [ "${script#/}" != "$script" ]; then
        d="$(dirname "$script")"
    else
        d="$cwd"
    fi
    [ -n "${d:-}" ] && DIRS+=("$d")
    say "   PID $pid → папка: ${d:-неизвестно}"
done < <(ps -eo pid=,args= | awk '/python/ && /bot\.py/ {print $1, $0}' | sort -u)

if [ "${#PIDS[@]}" -eq 0 ]; then
    say "   живых копий не нашёл (бот сейчас не запущен)"
fi

if [ "${#DIRS[@]}" -gt 1 ]; then
    UNIQ="$(printf '%s\n' "${DIRS[@]}" | sort -u)"
    if [ "$(printf '%s\n' "$UNIQ" | wc -l)" -gt 1 ]; then
        say "   ВНИМАНИЕ: бот запущен из нескольких разных папок —"
        printf '%s\n' "$UNIQ" | sed 's/^/     • /'
        say "   Именно поэтому кнопки могли не меняться. Все лишние копии сейчас остановлю."
    fi
fi

if [ -n "$ARG" ]; then
    TARGET="$(cd "$ARG" 2>/dev/null && pwd)" || { say "Папка не найдена: $ARG"; exit 1; }
elif [ "${#DIRS[@]}" -gt 0 ]; then
    TARGET="${DIRS[0]}"
else
    TARGET="$SRC"
fi

hr
say "2. Папка, которую обновляю: $TARGET"
if [ ! -f "$TARGET/bot.py" ]; then
    say "   В этой папке нет bot.py — обновлять нечего."; exit 1
fi
if [ "$TARGET" = "$SRC" ]; then
    say "   (новый код уже лежит в этой же папке)"
fi

hr
say "3. Бэкап базы"
mkdir -p "$TARGET/data" "$TARGET/backups"
if [ -f "$TARGET/data/bot.db" ]; then
    cp "$TARGET/data/bot.db" "$TARGET/backups/bot-$STAMP.db"
    say "   сохранено: $TARGET/backups/bot-$STAMP.db"
else
    say "   базы нет — пропускаю"
fi

hr
say "4. Кладу новый код"
if [ "$TARGET" != "$SRC" ]; then
    for item in app bot.py requirements.txt; do
        [ -e "$SRC/$item" ] || continue
        rm -rf "$TARGET/$item.new"
        cp -a "$SRC/$item" "$TARGET/$item.new"
        rm -rf "$TARGET/$item"
        mv "$TARGET/$item.new" "$TARGET/$item"
        say "   обновлено: $item"
    done
    mkdir -p "$TARGET/deploy"
    cp -a "$SRC/deploy/." "$TARGET/deploy/" 2>/dev/null || true
else
    say "   код уже на месте"
fi
find "$TARGET" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

hr
say "5. Что теперь в меню (читаю прямо из файлов на диске)"
( cd "$TARGET" && python3 bot.py --menu ) || say "   не смог прочитать меню — смотри ошибку выше"

hr
say "6. Останавливаю старые копии"
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q '^mc-bot.service'; then
    systemctl stop mc-bot.service 2>/dev/null && say "   systemd: mc-bot.service остановлен"
fi
if [ -f "$TARGET/data/bot.pid" ]; then
    kill "$(cat "$TARGET/data/bot.pid")" 2>/dev/null && say "   остановлен по pid-файлу"
    rm -f "$TARGET/data/bot.pid"
fi

kill_pid() {
    local pid="$1" i
    kill -0 "$pid" 2>/dev/null || return 0
    kill "$pid" 2>/dev/null || true
    for i in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || return 0
        sleep 0.5
    done
    kill -9 "$pid" 2>/dev/null || true
    say "   PID $pid не отвечал — снят жёстко"
}
for pid in "${PIDS[@]:-}"; do
    [ -z "${pid:-}" ] && continue
    kill_pid "$pid"
    say "   остановлен PID $pid"
done
sleep 1

hr
say "7. Запускаю бота заново"
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q '^mc-bot.service'; then
    systemctl start mc-bot.service
    sleep 4
    if systemctl is-active --quiet mc-bot.service; then
        say "   systemd: бот работает"
    else
        say "   systemd: не поднялся, смотри: journalctl -u mc-bot -n 50"
    fi
else
    ( cd "$TARGET" && bash deploy/start-bot.sh )
fi

hr
say "8. Проверка"
sleep 3
ALIVE=0
while read -r pid args; do
    [ -z "${pid:-}" ] && continue
    case "$args" in *bot.py*--selftest*|*bot.py*--menu*) continue ;; esac
    ALIVE=$((ALIVE+1))
    say "   работает PID $pid"
done < <(ps -eo pid=,args= | awk '/python/ && /bot\.py/ {print $1, $0}' | sort -u)
[ "$ALIVE" -eq 0 ] && say "   НЕ работает: процесс не найден"

if [ -f "$TARGET/data/bot.log" ]; then
    say "   последние строки лога:"
    tail -n 8 "$TARGET/data/bot.log" | sed 's/^/     /'
fi

hr
say "ГОТОВО. Теперь открой бота и нажми /menu — придёт новое меню."
say "Проверка версии в Телеграме (для админа): /version"
say ""
say "Кнопки должны быть такие:"
say "   [ Нужна помощь ] [ Жалоба на игрока ]"
say "   [ Анкета ]       [ Поддержать сервер ]"
say "   [ О сервере ]"
hr
