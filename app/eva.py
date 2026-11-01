"""Управление античитом Eva на игровом сервере через RCON.

Команды доступны только администрации бота (как и /console, /rcon).
Плагин Eva должен быть установлен на сервере, RCON — включён (см. /rcon).
"""
import re

from . import rcon

MAX_LEN = 3500
COLOR = re.compile(r"[§&][0-9a-fk-orx]", re.IGNORECASE)

CHECKS = ("xray", "esp", "xray-heuristic", "client-mods", "autoclicker")

HELP = """Античит Eva (только для администрации)

/eva check <ник> — флаги, риск, клиент, история игрока
/eva report <ник> — отправить все данные игрока боту «Ева» в Telegram
/eva toggle <проверка> — включить/выключить проверку:
    xray, esp, xray-heuristic, client-mods, autoclicker
/eva setban <ник> <причина> — ручной бан через консоль сервера
/eva reload — перечитать конфиг плагина Eva
/eva bot test — проверить связь плагина с ботом «Ева»
/eva online — кто сейчас на сервере

Команды выполняются через RCON-консоль сервера.
Если связи нет: /rcon test и /rcon set <адрес> <порт> <пароль>."""


def clean(text):
    """Убираем §-цвета Minecraft, чтобы Telegram-сообщение было читаемым."""
    text = COLOR.sub("", text or "")
    return text.replace("\r", "").strip()


def run(db, command):
    """Выполнить команду в консоли сервера. Возвращает (ok, текст, канал)."""
    ok, out, channel = rcon.run_any(db, command)
    return ok, clean(out), channel


def _send(bot, chat_id, command):
    ok, out, channel = run(bot.db, command)
    if ok:
        head = "Eva" + (" · %s" % channel if channel else "")
        bot.tg.send(chat_id, "%s\n\n%s" % (head, (out or "(пусто)")[:MAX_LEN]))
    else:
        bot.tg.send(chat_id, "Eva: не удалось выполнить «%s»\n\n%s"
                    % (command, (out or "(нет ответа)")[:MAX_LEN]))
    return True


def handle(bot, chat_id, arg):
    """Обработка /eva ... (вызывается из админской ветки bot.command)."""
    parts = arg.split()
    sub = parts[0].lower() if parts else ""
    rest = " ".join(parts[1:]).strip()

    if sub in ("", "help", "помощь", "справка"):
        bot.tg.send(chat_id, HELP)
        return True
    if sub in ("online", "онлайн", "кто"):
        return _send(bot, chat_id, "list")
    if sub in ("reload", "перезагрузить") and not rest:
        return _send(bot, chat_id, "eva reload")
    if sub in ("check", "проверка", "флаги", "профиль", "инфо") and rest:
        return _send(bot, chat_id, "eva check %s" % rest)
    if sub in ("report", "отчёт", "отчет", "репорт") and rest:
        return _send(bot, chat_id, "eva report %s" % rest)
    if sub in ("toggle", "переключить", "вкл", "выкл", "переключи") and rest:
        name = rest.lower()
        if name not in CHECKS:
            bot.tg.send(chat_id, "Неизвестная проверка: %s\nДоступно: %s"
                        % (rest, ", ".join(CHECKS)))
            return True
        return _send(bot, chat_id, "eva toggle %s" % name)
    if sub in ("setban", "бан", "забанить") and len(parts) >= 3:
        target = parts[1]
        reason = " ".join(parts[2:])
        return _send(bot, chat_id, "eva setban %s %s" % (target, reason))
    if sub in ("bot", "бот") and rest.lower() in ("test", "тест", "проверка", "проверить"):
        return _send(bot, chat_id, "eva bot test")

    bot.tg.send(chat_id, "Использование: /eva help")
    return True
