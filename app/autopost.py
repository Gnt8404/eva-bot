"""Посты в каналы и автоответы от имени Евы (настраиваемый текст).

Идея: админ добавляет бота в свой канал/группу (право публикации), бот
запоминает чат и умеет публиковать туда посты. Для личных сообщений можно
включить автоответы: набор правил «ключевые слова -> текст» и текст по
умолчанию. Всё настраивается командами из лички; панель доступна админам.

Доступ: только администрация бота. Обычные игроки команды не видят и не
могут вызвать.
"""
import json
import logging

from . import config
from .tg import esc, keyboard

log = logging.getLogger("autopost")

COMMANDS = {"/post", "/пост", "/autopost", "/ap", "/autoreply", "/ar"}
PREFIX = "ap:"
LOCK_ID = int(getattr(config, "AUTOPOST_CHANNEL_ID", 0) or 0)
SIGNATURE = getattr(config, "AUTOPOST_SIGNATURE", "<Eva ии помощница>")
MAX_CHANNELS = 20
MAX_RULES = 50

CHANNELS_KEY = "autopost_channels"
TEXT_KEY = "autopost_text"
POST_ON_KEY = "autopost_on"
REPLY_ON_KEY = "autoreply_on"
RULES_KEY = "autoreply_rules"
DEFAULT_KEY = "autoreply_default"


# ------------------------------------------------------------------ хранилище
def _json_list(store, key):
    raw = store.get(key)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def channels(store):
    # Если канал закреплён в конфиге, он работает всегда — даже если файл
    # настроек на сервере не успел загрузиться.
    if LOCK_ID:
        return [{"id": LOCK_ID,
                 "title": getattr(config, "AUTOPOST_CHANNEL_TITLE",
                                  "Aetherlyn")}]
    return [c for c in _json_list(store, CHANNELS_KEY)
            if isinstance(c, dict) and c.get("id")]


def _sign(text):
    text = (text or "").rstrip()
    if SIGNATURE and SIGNATURE not in text:
        text += "\n\n" + SIGNATURE
    return text


def rules(store):
    return [r for r in _json_list(store, RULES_KEY)
            if isinstance(r, dict) and r.get("text")]


def add_chat(store, chat, single=False):
    """Добавить канал для постов. single=True — оставить только его.

    Если в конфиге закреплён конкретный канал (AUTOPOST_CHANNEL_ID), другие
    игнорируются."""
    cid = (chat or {}).get("id")
    if not cid:
        return None
    if LOCK_ID:
        if int(cid) != LOCK_ID:
            return None
        return {"id": LOCK_ID,
                "title": getattr(config, "AUTOPOST_CHANNEL_TITLE", "Aetherlyn")}
    item = {"id": cid,
            "title": (chat or {}).get("title") or (chat or {}).get("username") or str(cid)}
    items = [] if single else [c for c in channels(store) if c.get("id") != cid]
    items.append(item)
    _save(store, CHANNELS_KEY, items)
    return item


def channel_from_forward(msg):
    """Канал из пересланного в бота поста (forward_from_chat / forward_origin)."""
    chat = (msg or {}).get("forward_from_chat")
    if not chat and (msg or {}).get("forward_origin"):
        origin = msg["forward_origin"] or {}
        chat = origin.get("chat") or origin.get("sender_chat")
    if not chat or (chat.get("type") not in ("channel", "supergroup")):
        return None
    return {"id": chat.get("id"), "title": chat.get("title") or chat.get("username")}


def _persist(store):
    try:
        from . import persist
        persist.save_autopost_async(store)
    except Exception:  # noqa: BLE001
        pass


def _set(store, key, value):
    store.set(key, value)
    _persist(store)


def _save(store, key, value):
    store.set(key, json.dumps(value, ensure_ascii=False))
    _persist(store)


def flag(store, key, default="on"):
    value = store.get(key)
    return (value if value is not None else default).lower() == "on"


def allowed(bot, uid, username=None):
    try:
        return bool(bot.db.is_admin(uid, username))
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ панель
def panel_text(store):
    chans = channels(store)
    rs = rules(store)
    default = store.get(DEFAULT_KEY) or ""
    return ("Посты и автоответы\n\n"
            "Посты: %s. Канал: %s (только он)\n"
            "Автоответы в личке: %s, правил: %d\n"
            "Текст поста: %s\n"
            "Ответ по умолчанию: %s\n\n"
            "Как подключить канал:\n"
            "1) Добавь @%s в канал и дай право «Публикация сообщений».\n"
            "2) Напиши: /post добавить @канал\n"
            "3) Пост: /post текст <текст> и /post отправить.\n\n"
            "Автоответы: /ar on, /ar добавить привет|Здравствуй! Чем помочь?\n"
            "/ar по-умолчанию <текст>, /ar список, /ar off"
            % ("вкл" if flag(store, POST_ON_KEY) else "выкл",
               (chans[0].get("title") if chans else "не подключён"),
               "вкл" if flag(store, REPLY_ON_KEY) else "выкл", len(rs),
               (store.get(TEXT_KEY) or "не задан")[:80],
               (default or "не задан")[:80],
               "RM_AI23bot"))


def panel_kb(store):
    return keyboard([
        [("Посты: %s" % ("вкл" if flag(store, POST_ON_KEY) else "выкл"), "ap:post_on"),
         ("Автоответы: %s" % ("вкл" if flag(store, REPLY_ON_KEY) else "выкл"), "ap:reply_on")],
        [("Список каналов", "ap:channels"), ("Правила ответов", "ap:rules")],
        [("Опубликовать текст", "ap:send")],
        [("Как подключить", "ap:help")],
        [("Админ-панель", "adm:home")],
    ])


def panel(bot, chat_id, uid=None):
    bot.tg.send(chat_id, panel_text(bot.db), keyboard=panel_kb(bot.db))


def help_text():
    return ("Подключение Евы к твоему каналу/группе\n\n"
            "1. Добавь @RM_AI23bot в канал или группу.\n"
            "2. Дай боту право «Публикация сообщений» (для канала — администратор).\n"
            "3. В личке с ботом: /post добавить @канал\n"
            "   (@канал можно заменить на id вида -1001234567890).\n"
            "4. Задай текст: /post текст Привет! Это новости Aetherlyn.\n"
            "5. Опубликуй: /post отправить\n\n"
            "Автоответы людям: /ar on, затем правила\n"
            "/ar добавить привет,здравствуй|Здравствуй! Чем помочь?\n"
            "Если правило не совпало — уйдёт текст «по умолчанию»:\n"
            "/ar по-умолчанию Напиши вопрос, я помогу.\n\n"
            "Команды доступны только администрации бота.")


# ------------------------------------------------------------------ публикация
def publish(bot, store, text=None):
    """Опубликовать текст во все сохранённые чаты. Возвращает (ok, fail)."""
    body = _sign((text or store.get(TEXT_KEY) or "").strip())
    if not body:
        return 0, 0
    if not flag(store, POST_ON_KEY):
        return 0, 0
    ok = fail = 0
    for ch in channels(store):
        try:
            bot.tg.send(ch["id"], body, preview=False)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            fail += 1
            log.info("пост в %s не ушёл: %s", ch.get("id"), exc)
    return ok, fail


def _resolve_channel(bot, raw):
    raw = (raw or "").strip()
    if not raw:
        return None, "Пусто. Пришли @канал или id."
    target = raw if raw.startswith("@") or raw.lstrip("-").isdigit() else "@" + raw
    try:
        info = bot.tg.call("getChat", chat_id=target)
    except Exception as exc:  # noqa: BLE001
        return None, "Не нашёл чат: %s" % str(getattr(exc, "description", "") or exc)[:150]
    cid = info.get("id")
    if not cid:
        return None, "Не нашёл чат."
    title = info.get("title") or info.get("username") or str(cid)
    try:
        me = (bot.me or {}).get("id")
        member = bot.tg.call("getChatMember", chat_id=cid, user_id=me)
        status = (member or {}).get("status")
        if status not in ("administrator", "creator"):
            return None, ("Бот не админ в «%s». Добавь его админом с правом "
                          "публикации сообщений." % title)
    except Exception:
        pass  # проверка прав не всегда доступна — не блокируем
    return {"id": cid, "title": title}, ""


# ------------------------------------------------------------------ автоответы
def reply_text(store, text):
    """Текст автоответа без привязки к флагу бота (для userbot)."""
    low = (text or "").lower().replace("ё", "е")
    for rule in rules(store):
        keys = rule.get("keys") or []
        if any((k or "").lower().replace("ё", "е") in low for k in keys if k):
            return _sign(rule.get("text"))
    default = (store.get(DEFAULT_KEY) or
               getattr(config, "AUTOREPLY_DEFAULT_TEXT", "")).strip()
    return _sign(default) if default else None


def match_reply(store, text):
    """Текст автоответа по правилам. None — если автоответы выключены."""
    if not flag(store, REPLY_ON_KEY):
        return None
    return reply_text(store, text)


# ------------------------------------------------------------------ команды
def is_command(cmd):
    return cmd in COMMANDS


def handle_command(bot, msg, text, chat_id, user, cmd, arg):
    uid = user.get("id")
    if not allowed(bot, uid, user.get("username")):
        bot.tg.send(chat_id, "Эта команда только для администрации бота.")
        return True
    chat_type = (msg.get("chat") or {}).get("type") or "private"
    if chat_type != "private":
        bot.tg.send(chat_id, "Настройка постов и автоответов — в личке с ботом.")
        return True

    low = (arg or "").strip().lower()
    parts = (arg or "").strip().split(None, 1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if not arg:
        panel(bot, chat_id, uid)
        return True
    if low in ("помощь", "help", "как", "подключить"):
        bot.tg.send(chat_id, help_text())
        return True

    if cmd in ("/post", "/пост", "/autopost", "/ap"):
        if sub in ("добавить", "add", "+"):
            if not rest:
                bot.tg.send(chat_id, "Напиши: /post добавить @канал")
                return True
            if len(channels(bot.db)) >= MAX_CHANNELS:
                bot.tg.send(chat_id, "Слишком много чатов (максимум %d)." % MAX_CHANNELS)
                return True
            ch, err = _resolve_channel(bot, rest)
            if err:
                bot.tg.send(chat_id, err)
                return True
            items = channels(bot.db)
            if any(c["id"] == ch["id"] for c in items):
                bot.tg.send(chat_id, "Этот чат уже добавлен.")
                return True
            items.append(ch)
            _save(bot.db, CHANNELS_KEY, items)
            bot.tg.send(chat_id, "Чат «%s» добавлен. Текст: /post текст …"
                                  % esc(ch["title"]), keyboard=panel_kb(bot.db))
            return True
        if sub in ("список", "list", "чаты"):
            items = channels(bot.db)
            if not items:
                bot.tg.send(chat_id, "Пока нет чатов. /post добавить @канал")
                return True
            lines = ["Чаты для постов:"]
            for i, ch in enumerate(items, 1):
                lines.append("%d. %s (%s)" % (i, esc(ch.get("title")), ch["id"]))
            lines.append("\nУбрать: /post удалить <номер>")
            bot.tg.send(chat_id, "\n".join(lines))
            return True
        if sub in ("удалить", "del", "-"):
            try:
                idx = int(rest) - 1
            except ValueError:
                bot.tg.send(chat_id, "Напиши: /post удалить <номер из списка>")
                return True
            items = channels(bot.db)
            if not (0 <= idx < len(items)):
                bot.tg.send(chat_id, "Нет такого номера.")
                return True
            gone = items.pop(idx)
            _save(bot.db, CHANNELS_KEY, items)
            bot.tg.send(chat_id, "Убрал «%s»." % esc(gone.get("title")))
            return True
        if sub in ("вкл", "on", "включить"):
            _set(bot.db, POST_ON_KEY, "on")
            bot.tg.send(chat_id, "Посты включены.", keyboard=panel_kb(bot.db))
            return True
        if sub in ("выкл", "off", "выключить"):
            _set(bot.db, POST_ON_KEY, "off")
            bot.tg.send(chat_id, "Посты выключены.", keyboard=panel_kb(bot.db))
            return True
        if sub in ("отправить", "send", "сейчас", "опубликовать"):
            if not flag(bot.db, POST_ON_KEY):
                _set(bot.db, POST_ON_KEY, "on")
            ok, fail = publish(bot, bot.db)
            if not ok and not fail:
                bot.tg.send(chat_id, "Некуда или нечего публиковать: добавь чат и текст.")
            else:
                bot.tg.send(chat_id, "Пост: доставлено %d, ошибок %d." % (ok, fail))
            return True
        if sub in ("текст", "text"):
            if not rest:
                bot.tg.send(chat_id, "Напиши: /post текст <что опубликовать>")
                return True
            _set(bot.db, TEXT_KEY, rest[:3500])
            bot.tg.send(chat_id, "Текст сохранён. Опубликовать: /post отправить",
                        keyboard=panel_kb(bot.db))
            return True
        # всё остальное — публикация текста сразу
        body = (arg or "").strip()
        if body:
            _set(bot.db, TEXT_KEY, body[:3500])
            if not flag(bot.db, POST_ON_KEY):
                _set(bot.db, POST_ON_KEY, "on")
            ok, fail = publish(bot, bot.db, body)
            bot.tg.send(chat_id, "Пост: доставлено %d, ошибок %d." % (ok, fail))
        return True

    # автоответы
    if sub in ("вкл", "on", "включить"):
        _set(bot.db, REPLY_ON_KEY, "on")
        bot.tg.send(chat_id, "Автоответы включены.", keyboard=panel_kb(bot.db))
        return True
    if sub in ("выкл", "off", "выключить"):
        _set(bot.db, REPLY_ON_KEY, "off")
        bot.tg.send(chat_id, "Автоответы выключены.", keyboard=panel_kb(bot.db))
        return True
    if sub in ("по-умолчанию", "default", "поумолчанию"):
        if not rest:
            bot.tg.send(chat_id, "Напиши: /ar по-умолчанию <текст ответа>")
            return True
        _set(bot.db, DEFAULT_KEY, rest[:1500])
        bot.tg.send(chat_id, "Ответ по умолчанию сохранён.")
        return True
    if sub in ("добавить", "add", "+"):
        if "|" not in rest:
            bot.tg.send(chat_id, "Формат: /ar добавить привет,здравствуй|Здравствуй! Чем помочь?")
            return True
        keys_raw, reply = rest.split("|", 1)
        keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
        if not keys or not reply.strip():
            bot.tg.send(chat_id, "Нужны и ключевые слова, и текст ответа.")
            return True
        items = rules(bot.db)
        if len(items) >= MAX_RULES:
            bot.tg.send(chat_id, "Слишком много правил (максимум %d)." % MAX_RULES)
            return True
        items.append({"keys": keys, "text": reply.strip()[:1500]})
        _save(bot.db, RULES_KEY, items)
        bot.tg.send(chat_id, "Правило добавлено (ключи: %s)." % ", ".join(keys[:5]),
                    keyboard=panel_kb(bot.db))
        return True
    if sub in ("список", "list"):
        items = rules(bot.db)
        if not items:
            bot.tg.send(chat_id, "Правил пока нет. /ar добавить …")
            return True
        lines = ["Правила автоответов:"]
        for i, rule in enumerate(items, 1):
            lines.append("%d. %s -> %s" % (i, ", ".join(rule.get("keys") or []),
                                          (rule.get("text") or "")[:120]))
        lines.append("\nТекст по умолчанию: %s" % (bot.db.get(DEFAULT_KEY) or "нет"))
        bot.tg.send(chat_id, "\n".join(lines))
        return True
    if sub in ("удалить", "del", "-"):
        try:
            idx = int(rest) - 1
        except ValueError:
            bot.tg.send(chat_id, "Напиши: /ar удалить <номер>")
            return True
        items = rules(bot.db)
        if not (0 <= idx < len(items)):
            bot.tg.send(chat_id, "Нет такого номера.")
            return True
        items.pop(idx)
        _save(bot.db, RULES_KEY, items)
        bot.tg.send(chat_id, "Правило удалено.")
        return True
    if sub in ("очистить", "clear", "сброс"):
        _save(bot.db, RULES_KEY, [])
        _set(bot.db, DEFAULT_KEY, "")
        bot.tg.send(chat_id, "Правила и ответ по умолчанию очищены.")
        return True

    panel(bot, chat_id, uid)
    return True


# ------------------------------------------------------------------ кнопки
def handle_callback(bot, data, chat_id, user, cbq):
    if not data.startswith(PREFIX):
        return False
    uid = user.get("id")
    if not allowed(bot, uid, user.get("username")):
        bot.tg.answer_callback(cbq.get("id"), "Только для администрации.", True)
        return True
    action = data[len(PREFIX):]
    if action == "post_on":
        _set(bot.db, POST_ON_KEY, "off" if flag(bot.db, POST_ON_KEY) else "on")
    elif action == "reply_on":
        _set(bot.db, REPLY_ON_KEY, "off" if flag(bot.db, REPLY_ON_KEY) else "on")
    elif action == "send":
        ok, fail = publish(bot, bot.db)
        bot.tg.send(chat_id, "Пост: доставлено %d, ошибок %d." % (ok, fail))
    elif action == "channels":
        items = channels(bot.db)
        text = ("Чаты для постов:\n" + "\n".join(
            "%d. %s (%s)" % (i, esc(c.get("title")), c["id"])
            for i, c in enumerate(items, 1))) if items else "Пока нет чатов."
        bot.tg.send(chat_id, text + "\n\nДобавить: /post добавить @канал")
    elif action == "rules":
        items = rules(bot.db)
        text = ("Правила:\n" + "\n".join(
            "%d. %s -> %s" % (i, ", ".join(r.get("keys") or []), (r.get("text") or "")[:90])
            for i, r in enumerate(items, 1))) if items else "Правил пока нет."
        bot.tg.send(chat_id, text + "\n\nДобавить: /ar добавить ключ|текст")
    elif action == "help":
        bot.tg.send(chat_id, help_text())
    panel(bot, chat_id, uid)
    return True
