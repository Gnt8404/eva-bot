"""Рассылка сообщений по чатам: панель настройки, выбор получателей, отправка.

Доступ: только id из config.BROADCAST_ADMINS (владелец сервера и доверенный
помощник). Всё остальное бот на эту команду отвечает «доступно владельцу».

Как это работает
----------------
1. Администратор пишет /рассылка — открывается панель настройки.
2. В панели задаётся сообщение (текст или фото с подписью), получатели
   (все чаты или выбранные галочками), пауза между отправками, тихий режим,
   закрепление, превью ссылок и необязательная кнопка-ссылка.
3. «Предпросмотр» присылает сообщение так, как его увидят чаты.
4. «Отправить» — подтверждение, затем фоновая рассылка с прогрессом и стопом.

Черновик хранится в базе (settings), поэтому переживает перезапуск бота.
"""
import json
import logging
import threading
import time

from . import config
from .tg import esc, keyboard

log = logging.getLogger("broadcast")

COMMANDS = {"/рассылка", "/rassylka", "/broadcast", "/bc", "/рассылки"}
DELAYS = [0.5, 1.0, 1.5, 2.0, 3.0, 5.0]
PAGE_SIZE = max(3, int(config.BROADCAST_PAGE))
FAIL_SHOW = 15

STOP = {}       # admin_id -> threading.Event
RUNNING = {}    # admin_id -> {"done": n, "total": n, "ok": n, "bad": [...]}
LOCK = threading.RLock()


# ----------------------------------------------------------------------
# доступ и черновик
# ----------------------------------------------------------------------
def allowed(uid):
    """Рассылка доступна только тем, кто перечислен в настройках."""
    try:
        return int(uid) in config.BROADCAST_ADMINS
    except (TypeError, ValueError):
        return False


def _key(uid, name):
    return "bc:%s:%s" % (name, uid)


def new_draft():
    return {
        "text": "",
        "photo": "",
        "button_text": "",
        "button_url": "",
        "mode": "selected",     # selected | all
        "targets": [],
        "delay": float(config.BROADCAST_DELAY),
        "silent": False,
        "pin": False,
        "preview": False,
    }


def box(store, uid):
    """Отдельная «комната» настроек: база + id администратора."""
    return _Room(store, uid)


class _Room:
    """Обёртка, чтобы не таскать store и uid в каждую функцию."""

    def __init__(self, store, uid):
        self.store = store
        self.uid = uid

    def draft(self):
        return load(self.store, self.uid)

    def save(self, draft):
        save(self.store, self.uid, draft)

    def wait(self):
        return (self.store.get(_key(self.uid, "wait")) or "").strip()

    def set_wait(self, value):
        self.store.set(_key(self.uid, "wait"), value or "")

    def reset(self):
        self.store.set(_key(self.uid, "draft"), "")
        self.store.set(_key(self.uid, "wait"), "")


def load(store, uid):
    draft = new_draft()
    raw = store.get(_key(uid, "draft")) or ""
    if not raw:
        return draft
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        log.info("черновик рассылки не разобрался: %s", exc)
        return draft
    if not isinstance(data, dict):
        return draft
    for field, default in draft.items():
        if field in data and data[field] is not None:
            draft[field] = data[field]
    try:
        draft["targets"] = sorted({int(x) for x in (draft.get("targets") or [])})
    except (TypeError, ValueError):
        draft["targets"] = []
    try:
        draft["delay"] = float(draft["delay"])
    except (TypeError, ValueError):
        draft["delay"] = float(config.BROADCAST_DELAY)
    for flag in ("silent", "pin", "preview"):
        draft[flag] = bool(draft[flag])
    if draft.get("mode") not in ("all", "selected"):
        draft["mode"] = "selected"
    return draft


def save(store, uid, draft):
    store.set(_key(uid, "draft"), json.dumps(draft, ensure_ascii=False))


def filled(draft):
    return bool((draft.get("text") or "").strip() or draft.get("photo"))


# ----------------------------------------------------------------------
# получатели
# ----------------------------------------------------------------------
def known(store):
    return store.chats()


def recipients(store, draft):
    rows = known(store)
    if draft.get("mode") == "all":
        return [r["chat_id"] for r in rows]
    chosen = set(draft.get("targets") or [])
    return [r["chat_id"] for r in rows if r["chat_id"] in chosen]


def describe_chat(row):
    title = (row.get("title") or "").strip() or str(row["chat_id"])
    if row.get("kind") == "private":
        return "личка: %s" % title
    return title


def parse_button(raw):
    """«Текст кнопки | https://ссылка» -> {text, url}."""
    raw = (raw or "").strip()
    if "|" not in raw:
        return None
    name, url = raw.split("|", 1)
    name, url = name.strip(), url.strip()
    if not url.lower().startswith(("http://", "https://", "tg://")):
        return None
    return {"text": (name or "Открыть")[:64], "url": url[:512]}


def biggest_photo(msg):
    photos = (msg or {}).get("photo") or []
    if not photos:
        return ""
    best = max(photos, key=lambda p: (p.get("width") or 0) * (p.get("height") or 0))
    return best.get("file_id") or ""


# ----------------------------------------------------------------------
# тексты панели
# ----------------------------------------------------------------------
def panel_text(bot, uid, draft=None):
    draft = draft if draft is not None else load(bot.db, uid)
    rows = known(bot.db)
    chosen = recipients(bot.db, draft)
    body = (draft.get("text") or "").strip()
    lines = ["<b>Рассылка по чатам</b>", ""]
    if body:
        short = body.replace("\n", " ")
        lines.append("Текст: %s%s" % (esc(short[:200]), "…" if len(short) > 200 else ""))
    else:
        lines.append("Текст: пока не задан")
    if draft.get("photo"):
        lines.append("Вложение: фото%s" % (" + подпись" if body else ""))
    mode = "все чаты" if draft.get("mode") == "all" else "выбранные чаты"
    lines.append("Получатели: %s — доступно %d, уйдёт %d"
                 % (mode, len(rows), len(chosen)))
    lines.append("Пауза между чатами: %.1f сек" % draft.get("delay", 1.0))
    lines.append("Тихий режим: %s" % ("вкл" if draft.get("silent") else "выкл"))
    lines.append("Закрепить сообщение: %s" % ("вкл" if draft.get("pin") else "выкл"))
    lines.append("Превью ссылок: %s" % ("вкл" if draft.get("preview") else "выкл"))
    if draft.get("button_url"):
        lines.append("Кнопка: «%s» → %s"
                     % (esc(draft.get("button_text") or "Открыть"), esc(draft["button_url"])))
    else:
        lines.append("Кнопка: нет")
    run = RUNNING.get(uid)
    if run:
        lines.append("")
        lines.append("Идёт отправка: %d из %d" % (run["done"], run["total"]))
    lines.append("")
    lines.append(esc("Ниже — что можно поменять. Текст можно прислать прямо сюда после кнопки "
                     "«Текст», либо командой /рассылка текст <сообщение>."))
    return "\n".join(lines)


def panel_kb(uid, draft=None):
    run = RUNNING.get(uid)
    if run:
        return keyboard([[("Остановить отправку", "bc:stop")],
                         [("Обновить", "bc:p")]])
    silent = "вкл" if draft.get("silent") else "выкл"
    pin = "вкл" if draft.get("pin") else "выкл"
    prev = "вкл" if draft.get("preview") else "выкл"
    return keyboard([
        [("Текст", "bc:text"), ("Фото", "bc:photo")],
        [("Получатели", "bc:list:0"), ("Все чаты", "bc:all"), ("Никого", "bc:none")],
        [("Пауза %.1fс" % draft.get("delay", 1.0), "bc:delay"),
         ("Тихий: %s" % silent, "bc:silent")],
        [("Закреп: %s" % pin, "bc:pin"), ("Кнопка", "bc:btn")],
        [("Превью ссылок: %s" % prev, "bc:preview")],
        [("Предпросмотр", "bc:show"), ("Отправить", "bc:go")],
        [("Сбросить", "bc:reset"), ("История", "bc:hist")],
    ])


def list_text(bot, uid, draft, page=0):
    rows = known(bot.db)
    chosen = set(draft.get("targets") or []) if draft.get("mode") != "all" else None
    total = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total - 1))
    lines = ["<b>Кому отправлять</b>", ""]
    if not rows:
        lines.append("Я пока не вижу ни одного чата. Добавь чат вручную кнопкой ниже "
                     "или дождись сообщения в группе — чаты появляются сами.")
    else:
        lines.append("Всего чатов в списке: %d. Страница %d из %d." % (len(rows), page + 1, total))
        lines.append("Нажми на чат, чтобы отметить или снять отметку.")
    if draft.get("mode") == "all":
        lines.append("")
        lines.append("Сейчас режим «все чаты» — уйдут во все, отметки не важны.")
    lines.append("")
    lines.append("Назад: /рассылка")
    return "\n".join(lines)


def list_kb(draft, rows, page=0):
    total = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total - 1))
    chunk = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    chosen = set(draft.get("targets") or [])
    marked_all = draft.get("mode") == "all"
    rows_kb = []
    for row in chunk:
        on = marked_all or row["chat_id"] in chosen
        title = describe_chat(row).replace("\n", " ")[:34]
        rows_kb.append([("%s %s" % ("✅" if on else "⬜", title), "bc:t:%d" % row["chat_id"])])
    if total > 1:
        rows_kb.append([("⏪", "bc:list:%d" % max(0, page - 1)),
                        ("%d/%d" % (page + 1, total), "bc:noop"),
                        ("⏩", "bc:list:%d" % min(total - 1, page + 1))])
    rows_kb.append([("Все чаты", "bc:all"), ("Никого", "bc:none")])
    rows_kb.append([("Добавить чат", "bc:add"), ("Назад", "bc:p")])
    return keyboard(rows_kb)


def confirm_text(bot, draft):
    rows = recipients(bot.db, draft)
    body = (draft.get("text") or "").strip()
    lines = ["<b>Отправить рассылку?</b>", ""]
    lines.append("Чатов: %d" % len(rows))
    lines.append("Пауза: %.1f сек, всего примерно %s"
                 % (draft.get("delay", 1.0), human_time(len(rows) * draft.get("delay", 1.0) + 1)))
    if body:
        lines.append("")
        lines.append("Сообщение:")
        lines.append(esc(body[:800]))
    if draft.get("photo"):
        lines.append("")
        lines.append("+ фото")
    if draft.get("button_url"):
        lines.append("+ кнопка «%s»" % esc(draft.get("button_text") or "Открыть"))
    lines.append("")
    lines.append("Отменить можно кнопкой ниже — до старта ничего не уходит.")
    return "\n".join(lines)


def human_time(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return "%d сек" % seconds
    return "%d мин" % max(1, seconds // 60)


def help_text():
    return ("Рассылка по чатам — как пользоваться:\n\n"
            "/рассылка — открыть панель настройки\n"
            "/рассылка текст <сообщение> — задать текст\n"
            "/рассылка кому — выбрать чаты\n"
            "/рассылка отправить — подтвердить и запустить\n"
            "/рассылка история — что уже рассылали\n"
            "/рассылка сброс — очистить черновик\n\n"
            "В панели настраиваются: текст или фото, получатели, пауза между чатами, "
            "тихий режим, закрепление, превью ссылок и кнопка-ссылка под сообщением. "
            "Команда доступна только владельцу сервера.")


def history_text(bot, limit=5):
    rows = bot.db.broadcast_history(limit)
    if not rows:
        return "Рассылок пока не было."
    lines = ["Последние рассылки:"]
    for row in rows:
        lines.append("#%d %s — %s: доставлено %d, ошибок %d"
                     % (row["id"], time.strftime("%d.%m %H:%M", time.localtime(row["created_at"])),
                        "завершена" if row["status"] == "done" else row["status"],
                        row["sent"] or 0, row["failed"] or 0))
        body = (row.get("text") or "").replace("\n", " ")
        if body:
            lines.append("   %s" % body[:90])
    return "\n".join(lines)


# ----------------------------------------------------------------------
# отправка
# ----------------------------------------------------------------------
def _payload(chat_id, draft):
    button = None
    if draft.get("button_url"):
        button = {"inline_keyboard": [[{"text": draft.get("button_text") or "Открыть",
                                        "url": draft["button_url"]}]]}
    body = (draft.get("text") or "").strip()
    if draft.get("photo"):
        params = {
            "chat_id": chat_id,
            "photo": draft["photo"],
            "caption": body[:1000] or None,
            "disable_notification": True if draft.get("silent") else None,
        }
        if button:
            params["reply_markup"] = json.dumps(button, ensure_ascii=False)
        return "sendPhoto", {k: v for k, v in params.items() if v is not None}
    params = {
        "chat_id": chat_id,
        "text": body,
        "disable_web_page_preview": not draft.get("preview"),
        "disable_notification": True if draft.get("silent") else None,
    }
    if button:
        params["reply_markup"] = json.dumps(button, ensure_ascii=False)
    return "sendMessage", {k: v for k, v in params.items() if v is not None}


def send_to(bot, chat_id, draft):
    """Отправляет одно сообщение. Возвращает (ок, message_id|причина)."""
    method, params = _payload(chat_id, draft)
    try:
        res = bot.tg.call(method, **params)
    except Exception as exc:  # noqa: BLE001
        reason = str(getattr(exc, "description", "") or exc)[:120]
        return False, reason
    message_id = (res or {}).get("message_id")
    if draft.get("pin") and message_id:
        try:
            bot.tg.call("pinChatMessage", chat_id=chat_id, message_id=message_id,
                        disable_notification=True)
        except Exception as exc:  # noqa: BLE001
            log.info("закрепить не вышло (%s): %s", chat_id, exc)
    return True, message_id


def snapshot(bot, draft):
    """Присылает администратору предпросмотр ровно так, как увидят чаты."""
    chat_id = draft["_chat"]
    body = (draft.get("text") or "").strip()
    if not filled(draft):
        bot.tg.send(chat_id, "Пока нечего показывать — задай текст или фото.")
        return
    method, params = _payload(chat_id, draft)
    params.pop("disable_notification", None)
    try:
        bot.tg.call(method, **params)
    except Exception as exc:  # noqa: BLE001
        bot.tg.send(chat_id, "Предпросмотр не получился: %s"
                    % str(getattr(exc, "description", "") or exc)[:200])
        return
    bot.tg.send(chat_id, "Так это увидят чаты.")


def start(bot, uid, chat_id, panel_message_id=None):
    draft = load(bot.db, uid)
    if not filled(draft):
        bot.tg.send(chat_id, "Сначала задай текст или фото — кнопка «Текст» в панели.")
        return
    rows = recipients(bot.db, draft)
    if not rows:
        bot.tg.send(chat_id, "Получатели не выбраны. Кнопка «Получатели» в панели.")
        return
    with LOCK:
        if uid in RUNNING:
            bot.tg.send(chat_id, "Рассылка уже идёт — сначала останови текущую.")
            return
        stop = threading.Event()
        STOP[uid] = stop
        RUNNING[uid] = {"done": 0, "total": len(rows), "ok": 0, "bad": []}
    bid = bot.db.add_broadcast(uid, bot.db.chat_name(uid), draft.get("text") or "(фото)",
                               rows)
    threading.Thread(target=_run, args=(bot, uid, chat_id, panel_message_id, draft, rows,
                                        stop, bid), daemon=True).start()


def _run(bot, uid, chat_id, panel_message_id, draft, rows, stop, bid):
    ok = 0
    bad = []
    last_paint = 0.0
    try:
        for index, cid in enumerate(rows, 1):
            if stop.is_set():
                break
            sent, info = send_to(bot, cid, draft)
            if sent:
                ok += 1
            else:
                bad.append((cid, info))
            with LOCK:
                state = RUNNING.get(uid)
                if state:
                    state["done"] = index
                    state["ok"] = ok
                    state["bad"] = bad
            now = time.time()
            if panel_message_id and (now - last_paint > 3 or index == len(rows)):
                last_paint = now
                _paint_progress(bot, chat_id, panel_message_id, index, len(rows), ok, len(bad))
            if index < len(rows):
                time.sleep(max(0.2, float(draft.get("delay", 1.0))))
    except Exception as exc:  # noqa: BLE001
        log.exception("рассылка упала: %s", exc)
        bad.append((0, "сбой отправки"))
    finally:
        stopped = stop.is_set()
        bot.db.finish_broadcast(bid, "stopped" if stopped else "done", ok, len(bad))
        report = ["<b>Рассылка %s</b>" % ("остановлена" if stopped else "завершена"), ""]
        report.append("Доставлено: %d" % ok)
        report.append("Не доставлено: %d" % len(bad))
        if bad:
            report.append("")
            report.append("Где не получилось:")
            for cid, reason in bad[:FAIL_SHOW]:
                report.append("• %s — %s" % (esc(bot.db.chat_name(cid) if cid else "—"),
                                             esc(reason)))
            if len(bad) > FAIL_SHOW:
                report.append("…и ещё %d" % (len(bad) - FAIL_SHOW))
        text = "\n".join(report)
        panel_kb = keyboard([[("Новая рассылка", "bc:p")]])
        painted = False
        if panel_message_id:
            try:
                bot.tg.edit(chat_id, panel_message_id, text, keyboard=panel_kb, html=True)
                painted = True
            except Exception as exc:  # noqa: BLE001
                log.info("итог рассылки не отредактировался: %s", exc)
        if not painted:
            bot.tg.send_html(chat_id, text, keyboard=panel_kb)
        with LOCK:
            STOP.pop(uid, None)
            RUNNING.pop(uid, None)


def _paint_progress(bot, chat_id, message_id, done, total, ok, failed):
    text = ("Идёт отправка: %d из %d\nДоставлено: %d, ошибок: %d\n\n"
            "Можно остановить — уже отправленное останется." % (done, total, ok, failed))
    try:
        bot.tg.edit(chat_id, message_id, text,
                    keyboard=keyboard([[("Остановить отправку", "bc:stop")]]))
    except Exception as exc:  # noqa: BLE001
        log.info("прогресс рассылки не обновился: %s", exc)


# ----------------------------------------------------------------------
# ввод от администратора
# ----------------------------------------------------------------------
CANCEL_WORDS = ("отмена", "/cancel", "стоп", "назад", "cancel")


def capture(bot, msg, text, user):
    """Ловит следующее сообщение администратора, если панель его ждёт."""
    uid = (user or {}).get("id")
    if not allowed(uid):
        return False
    room = box(bot.db, uid)
    wait = room.wait()
    if not wait:
        return False
    chat_id = msg["chat"]["id"]
    raw = (text or "").strip()
    if raw.lower() in CANCEL_WORDS:
        room.set_wait("")
        room_panel(bot, uid, chat_id, "Отменено.")
        return True

    draft = room.draft()
    if wait == "text":
        body = raw or (msg.get("text") or msg.get("caption") or "").strip()
        photo = biggest_photo(msg)
        if not body and not photo:
            bot.tg.send(chat_id, "Нужен текст, фото или подпись к фото. "
                                 "Или напиши «отмена».")
            return True
        if photo:
            draft["photo"] = photo
        if body:
            draft["text"] = body[:3800]
        room.save(draft)
        room.set_wait("")
        room_panel(bot, uid, chat_id, "Записал. Так это выглядит в настройках:")
        return True

    if wait == "photo":
        photo = biggest_photo(msg)
        if not photo:
            bot.tg.send(chat_id, "Пришли именно картинку — или напиши «отмена».")
            return True
        draft["photo"] = photo
        caption = (msg.get("caption") or "").strip()
        if caption:
            draft["text"] = caption[:3800]
        room.save(draft)
        room.set_wait("")
        room_panel(bot, uid, chat_id, "Фото прикреплено. Так это выглядит в настройках:")
        return True

    if wait == "button":
        if raw.lower() in ("нет", "убрать", "снять", "нет кнопки", "-", "0"):
            draft["button_text"] = ""
            draft["button_url"] = ""
            room.save(draft)
            room.set_wait("")
            room_panel(bot, uid, chat_id, "Кнопку убрал.")
            return True
        parsed = parse_button(raw)
        if not parsed:
            bot.tg.send(chat_id, "Формат такой: <текст кнопки> | <ссылка>\n"
                                 "Например: Открыть чат | https://t.me/+8QHP3S2DRsAzNjli\n"
                                 "Или напиши «нет», чтобы убрать кнопку.")
            return True
        draft["button_text"] = parsed["text"]
        draft["button_url"] = parsed["url"]
        room.save(draft)
        room.set_wait("")
        room_panel(bot, uid, chat_id, "Кнопка сохранена.")
        return True

    if wait == "add":
        cid, error = resolve_chat(bot, raw)
        if error:
            bot.tg.send(chat_id, error)
            return True
        row = bot.db.chat(cid) or {"title": str(cid)}
        draft["targets"] = sorted(set(draft.get("targets") or []) | {cid})
        if draft.get("mode") == "all":
            draft["mode"] = "selected"
        room.save(draft)
        room.set_wait("")
        room_panel(bot, uid, chat_id, "Чат «%s» добавлен в получатели." % esc(row.get("title")))
        return True

    room.set_wait("")
    return False


def resolve_chat(bot, raw):
    """Принимает @юзернейм или числовой id и пробует получить из Telegram."""
    raw = (raw or "").strip()
    if not raw:
        return 0, "Пусто. Пришли @юзернейм или числовой id."
    target = raw if raw.startswith("@") \
        else (int(raw) if raw.lstrip("-").isdigit() else "@" + raw.lstrip("@"))
    try:
        info = bot.tg.call("getChat", chat_id=target)
    except Exception as exc:  # noqa: BLE001
        return 0, ("Не нашёл такой чат. Проверь @юзернейм или возьми id чата. (%s)"
                   % str(getattr(exc, "description", "") or exc)[:120])
    if not info or not info.get("id"):
        return 0, "Не нашёл такой чат."
    bot.db.touch_chat(info)
    return info["id"], ""


# ----------------------------------------------------------------------
# панель
# ----------------------------------------------------------------------
def room_panel(bot, uid, chat_id, headline="", message_id=None):
    """Показывает панель: с заголовком — новым сообщением, без — правит прежнее."""
    draft = load(bot.db, uid)
    text = panel_text(bot, uid, draft)
    if headline:
        text = headline + "\n\n" + text
    kb = panel_kb(uid, draft)
    if message_id:
        try:
            bot.tg.edit(chat_id, message_id, text, keyboard=kb, html=True)
            return message_id
        except Exception as exc:  # noqa: BLE001
            log.info("панель рассылки не отредактировалась: %s", exc)
    sent = bot.tg.send_html(chat_id, text, keyboard=kb)
    return (sent or {}).get("message_id")


def show_list(bot, uid, chat_id, page=0, message_id=None):
    draft = load(bot.db, uid)
    rows = known(bot.db)
    text = list_text(bot, uid, draft, page)
    kb = list_kb(draft, rows, page)
    if message_id:
        try:
            bot.tg.edit(chat_id, message_id, text, keyboard=kb, html=True)
            return message_id
        except Exception as exc:  # noqa: BLE001
            log.info("список чатов не отредактировался: %s", exc)
    sent = bot.tg.send_html(chat_id, text, keyboard=kb)
    return (sent or {}).get("message_id")


# ----------------------------------------------------------------------
# команда
# ----------------------------------------------------------------------
def is_command(cmd):
    return cmd in COMMANDS


def handle_command(bot, msg, text, chat_id, user, cmd):
    uid = user.get("id")
    chat_type = (msg.get("chat") or {}).get("type") or "private"
    if not allowed(uid):
        bot.tg.send(chat_id, "Рассылка доступна только владельцу сервера.")
        return True
    if chat_type != "private":
        bot.tg.send(chat_id, "%s, рассылка настраивается в личке — напиши мне @%s"
                     % (user.get("first_name") or "Друг", config.BOT_USERNAME))
        return True

    parts = text.split(None, 1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    low = arg.lower()
    room = box(bot.db, uid)

    if low in ("сброс", "отмена", "очистить", "clear", "reset"):
        room.reset()
        room_panel(bot, uid, chat_id, "Черновик очищен.")
        return True
    if low in ("отправить", "запуск", "send", "go"):
        draft = load(bot.db, uid)
        bot.tg.send_html(chat_id, confirm_text(bot, draft),
                         keyboard=keyboard([[("Да, отправить", "bc:go2")],
                                            [("Нет, назад", "bc:p")]]))
        return True
    if low in ("кому", "получатели", "список", "list", "цели"):
        show_list(bot, uid, chat_id, 0)
        return True
    if low in ("история", "history", "статистика"):
        bot.tg.send(chat_id, history_text(bot), keyboard=keyboard([[("Назад", "bc:p")]]))
        return True
    if low in ("помощь", "help", "?", "как"):
        bot.tg.send(chat_id, help_text())
        return True
    if low.startswith("текст") or low.startswith("text"):
        body = arg[len("текст"):].strip() if low.startswith("текст") else arg[4:].strip()
        if body:
            draft = load(bot.db, uid)
            draft["text"] = body[:3800]
            save(bot.db, uid, draft)
            room_panel(bot, uid, chat_id, "Текст записан.")
            return True
        room.set_wait("text")
        bot.tg.send(chat_id, "Пришли одним сообщением то, что нужно разослать. "
                             "Можно с фото — тогда подпись станет текстом. «отмена» — выход.")
        return True
    if arg:
        draft = load(bot.db, uid)
        draft["text"] = arg[:3800]
        save(bot.db, uid, draft)
        room_panel(bot, uid, chat_id, "Текст записан.")
        return True

    room_panel(bot, uid, chat_id)
    return True


# ----------------------------------------------------------------------
# кнопки
# ----------------------------------------------------------------------
def handle_callback(bot, data, chat_id, user, cbq):
    if not data.startswith("bc:"):
        return False
    uid = (user or {}).get("id")
    msg = cbq.get("message") or {}
    message_id = msg.get("message_id")
    if not allowed(uid):
        bot.tg.answer_callback(cbq.get("id"), "Рассылка доступна только владельцу.", True)
        return True
    action = data[3:]
    room = box(bot.db, uid)
    draft = room.draft()

    if action == "noop":
        return True
    if action == "p":
        room_panel(bot, uid, chat_id, message_id=message_id)
        return True
    if action == "reset":
        room.reset()
        room_panel(bot, uid, chat_id, "Черновик очищен.", message_id=message_id)
        return True
    if action == "hist":
        try:
            bot.tg.edit(chat_id, message_id, history_text(bot),
                        keyboard=keyboard([[("Назад", "bc:p")]]))
        except Exception as exc:  # noqa: BLE001
            log.info("история рассылок не показалась: %s", exc)
        return True
    if action == "text":
        room.set_wait("text")
        bot.tg.send(chat_id, "Пришли сообщение для рассылки — текст или фото с подписью. "
                             "Поддерживаются переносы строк. «отмена» — выход.")
        return True
    if action == "photo":
        room.set_wait("photo")
        bot.tg.send(chat_id, "Пришли фото. Если у фото будет подпись — она станет текстом.")
        return True
    if action == "btn":
        room.set_wait("button")
        bot.tg.send(chat_id, "Кнопка под сообщением. Формат: <текст> | <ссылка>\n"
                             "Например: Вступить в Discord | https://discord.gg/5aqmYpKnE\n"
                             "«нет» — убрать кнопку.")
        return True
    if action == "add":
        room.set_wait("add")
        bot.tg.send(chat_id, "Пришли @юзернейм чата или его id — добавлю в получатели.")
        return True
    if action == "all":
        draft["mode"] = "all"
        save(bot.db, uid, draft)
        room_panel(bot, uid, chat_id, "Режим: все чаты.", message_id=message_id)
        return True
    if action == "none":
        draft["mode"] = "selected"
        draft["targets"] = []
        save(bot.db, uid, draft)
        room_panel(bot, uid, chat_id, "Получатели сняты.", message_id=message_id)
        return True
    if action.startswith("list:"):
        page = action[5:]
        page = int(page) if page.isdigit() else 0
        show_list(bot, uid, chat_id, page, message_id=message_id)
        return True
    if action.startswith("t:"):
        raw = action[2:]
        if raw.lstrip("-").isdigit():
            cid = int(raw)
            targets = set(draft.get("targets") or [])
            if cid in targets:
                targets.discard(cid)
            else:
                targets.add(cid)
            draft["targets"] = sorted(targets)
            draft["mode"] = "selected"
            save(bot.db, uid, draft)
            show_list(bot, uid, chat_id, 0, message_id=message_id)
        return True
    if action == "delay":
        current = draft.get("delay", 1.0)
        try:
            index = min(range(len(DELAYS)), key=lambda i: abs(DELAYS[i] - current))
        except ValueError:
            index = 0
        draft["delay"] = DELAYS[(index + 1) % len(DELAYS)]
        save(bot.db, uid, draft)
        room_panel(bot, uid, chat_id, "Пауза: %.1f сек." % draft["delay"],
                   message_id=message_id)
        return True
    if action in ("silent", "pin", "preview"):
        draft[action] = not bool(draft.get(action))
        save(bot.db, uid, draft)
        names = {"silent": "Тихий режим", "pin": "Закрепление", "preview": "Превью ссылок"}
        room_panel(bot, uid, chat_id,
                   "%s: %s" % (names[action], "вкл" if draft[action] else "выкл"),
                   message_id=message_id)
        return True
    if action == "show":
        view = dict(draft)
        view["_chat"] = chat_id
        snapshot(bot, view)
        return True
    if action == "go":
        if RUNNING.get(uid):
            room_panel(bot, uid, chat_id, "Рассылка уже идёт.", message_id=message_id)
            return True
        if not filled(draft):
            room_panel(bot, uid, chat_id, "Сначала задай текст или фото.",
                       message_id=message_id)
            return True
        bot.tg.send_html(chat_id, confirm_text(bot, draft),
                         keyboard=keyboard([[("Да, отправить", "bc:go2")],
                                            [("Нет, назад", "bc:p")]]))
        return True
    if action == "go2":
        bot.tg.answer_callback(cbq.get("id"), "Запускаю…")
        room.set_wait("")
        bot.tg.edit(chat_id, message_id, "Рассылка запущена.",
                    keyboard=keyboard([[("Остановить отправку", "bc:stop")]]))
        start(bot, uid, chat_id, message_id)
        return True
    if action == "stop":
        stop = STOP.get(uid)
        if stop:
            stop.set()
            bot.tg.answer_callback(cbq.get("id"), "Останавливаю…")
        else:
            bot.tg.answer_callback(cbq.get("id"), "Сейчас ничего не отправляется.")
        return True
    room_panel(bot, uid, chat_id, message_id=message_id)
    return True
