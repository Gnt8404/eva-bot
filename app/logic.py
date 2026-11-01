"""Логика бота: меню, формы, жалобы, модерация, ответы по фактам, админка."""
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import (ai, broadcast, config, crafts, eva, kb, mckb, mc, moder, music, rcon,
               sinfo, tg, web)
from .db import Store
from .tg import Tg, Typing, esc, keyboard, mention, strip_tags
from .typer import Typewriter, smooth_send

log = logging.getLogger("logic")

WAKE_RE = re.compile(
    r"(?<![а-яёa-z])(%s)(?![а-яёa-z])" % "|".join(re.escape(w) for w in config.WAKE_WORDS),
    re.IGNORECASE)

VIDEO_RE = re.compile(
    r"(видео|ролик|ютуб|youtube|туториал|tutorial|гайд|guide|обзор|"
    r"скинь\s+ссылк|дай\s+ссылк|найди\s+ссылк)", re.IGNORECASE)

SERVERQ_RE = re.compile(
    r"(ip|айпи|онлайн|он-лайн|сколько\s+игрок|верси[яю]|адрес\s+сервер|нашего\s+сервер|"
    r"на\s+сервере|нашем\s+сервер|подключит|зайти|лаги\s+на\s+сервер|статус)", re.IGNORECASE)

SEARCH_RE = re.compile(
    r"(найди|поищи|загугл|погугл|поиск|в\s+интернете|актуальн|"
    r"последн|нов(ый|ое|ая|ые)|что\s+такое|кто\s+такой|почему|сколько\s+стоит|"
    r"лучш(ий|ая|ее)|как\s+сделать\s+в\s+1\.|обновлен)", re.IGNORECASE)

CRAFT_RE = re.compile(r"(крафт|рецепт|скрафт|сделать|изготов|craft|recipe)", re.IGNORECASE)

# --- экономия токенов на второй линии модерации ---
# Модель зовём только когда в сообщении есть обращение к человеку или намёк
# на грубость. Обычная болтовня проверяется словарём, без запроса к ИИ.
AI_HINT_RE = re.compile(
    r"(?<![а-яёa-z])("
    r"дур|туп|глуп|идиот|дебил|кретин|урод|лох|нуб|клоун|крыс|свин|баран|мусор|дно|"
    r"школ|жир|слаб|бездар|отстой|чмо|задрот|обезьян|придур|мерз|мраз|ханж|подлец|"
    r"stupid|idiot|dumb|moron|noob|clown|trash|loser|fool|bastard|ugly|"
    r"aptal|salak|gerizekal|durak|kreten|glup"
    r")", re.IGNORECASE)

ADDRESS_RE = re.compile(
    r"(@[A-Za-z0-9_]{3,}|(?<![а-яёa-z])(ты|тебя|тебе|твой|тво[яйё]|твоё|вы|вас|вам|ваш|"
    r"you|your|du|sen|seni|sana|tu|te|vos)(?![а-яёa-z]))", re.IGNORECASE)

MAIN_MENU = keyboard([
    [("Нужна помощь", "m:help"), ("Жалоба на игрока", "m:complaint")],
    [("О сервере", "m:about"), ("Плагины и датапаки", "m:plugins")],
    [("Анкета", "m:form"), ("Поддержать сервер", "m:donate")],
])

# Версия кода. Видна в /version и в логе при старте — по ней сразу понятно,
# какой именно код сейчас работает на сервере.
CODE_VERSION = "6.6"

# Кнопки главного меню плоским списком (для лога, /version и самопроверки).
MENU_TITLES = [b["text"] for row in MAIN_MENU["inline_keyboard"] for b in row]

BACK_MENU = keyboard([[("В меню", "m:menu")]])

# ---------- заявка на помощь ----------
# код, как показывать в кнопке, по каким словам угадать категорию из текста
HELP_CATEGORIES = [
    ("join", "Не могу зайти", ("зайти", "заход", "подключ", "коннект", "не пуска",
                               "выкидыв", "кик", "соединен", "пинг", "лаунчер")),
    ("stuff", "Пропали вещи", ("вещ", "инвентар", "пропал", "потерял", "прогресс",
                               "сундук", "база", "дом", "смерт", "энд")),
    ("bug", "Баг или ошибка", ("баг", "ошибк", "глюч", "лаг", "не работает",
                               "краш", "вылет", "зависа", "дюп")),
    ("wl", "Вайтлист или ник", ("вайтлист", "whitelist", "белый список", "ник",
                                "никнейм", "сменить ник")),
    ("money", "Донат или оплата", ("донат", "оплат", "звезд", "деньг", "купить",
                                   "платеж", "перевод", "поддерж")),
    ("other", "Другое", ()),
]
HELP_CAT_TITLE = {code: title for code, title, _ in HELP_CATEGORIES}
HELP_BY_TEXT = [(code, words) for code, _, words in HELP_CATEGORIES if words]


def help_cat_keyboard():
    rows, row = [], []
    for code, title, _ in HELP_CATEGORIES:
        row.append((title, "h:cat:%s" % code))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("В меню", "m:menu")])
    return keyboard(rows)


def help_category(text):
    """Угадать категорию заявки по свободному тексту игрока."""
    low = (text or "").lower()
    best, score = "other", 0
    for code, words in HELP_BY_TEXT:
        hits = sum(1 for w in words if w in low)
        if hits > score:
            best, score = code, hits
    return best

ADMIN_ONLY = {
    "/admin", "/stats", "/админ", "/setinfo", "/настройка", "/setip", "/setbedrock", "/ip",
    "/admins", "/администраторы", "/addadmin", "/deladmin", "/cooldown", "/emotions", "/эмоции",
    "/emoji", "/эмодзи", "/insultdm", "/violations", "/fact", "/facts", "/delfact",
    "/tickets", "/zayavki", "/answer", "/say",
    "/console", "/cmd", "/консоль", "/rcon", "/wl", "/whitelist", "/вайтлист",
    "/eva", "/ева",
    "/kb", "/знания",
    "/moder", "/модерация", "/фильтр", "/check", "/проверка",
    "/addbad", "/delbad", "/badlist", "/badwords",
    "/accept", "/принять", "/reject", "/отклонить",
    "/helpq", "/card", "/take", "/close",
}

# команды заявок: доступны владельцу и доверенному помощнику
HELP_CMDS = {"/answer", "/card", "/helpq", "/take", "/close", "/tickets", "/zayavki"}

FORM_HELP_STEPS = [
    ("Ник в игре", "nick"),
    ("Что случилось и что нужно сделать", "problem"),
    ("Когда и при каких условиях это произошло", "when"),
    ("Что уже пробовал", "tried"),
    ("Скриншот или ссылка (можно приложить фото, либо напиши «нет»)", "proof"),
]

FORMS = {
    "help": {
        "title": "Заявка на помощь",
        "hint": "Отвечай одним сообщением на каждый пункт. Заявку увидит администрация сервера.",
        "steps": FORM_HELP_STEPS,
    },
    "complaint": {
        "title": "Жалоба на игрока",
        "hint": "Заполни по шагам. Отвечай одним сообщением на каждый пункт.",
        "steps": [
            ("Ник нарушителя", "target"),
            ("Что произошло (факты, время)", "reason"),
            ("Доказательства: ссылка на скрин или видео, либо «нет»", "proof"),
            ("Твой ник в игре", "nick"),
        ],
    },
    "form": {
        "title": "Анкета",
        "hint": "Заявка на вступление. Отвечай одним сообщением на каждый пункт.",
        "steps": [
            ("Ник в игре", "nick"),
            ("Возраст", "age"),
            ("Сколько играешь в Minecraft", "exp"),
            ("Почему хочешь играть именно у нас", "why"),
            ("Откуда узнал о сервере", "src"),
        ],
    },
}
# старые кнопки/команды со словом «поддержка» ведут на заявку на помощь
FORMS["support"] = FORMS["help"]

HELP_TEXT = """Что я умею:
1. Помогаю по серверу: адреса, версии, вайтлист, правила, онлайн, донат, ресурс-пак.
2. Знаю плагины, которыми пользуются игроки: что умеют и какие команды.
3. Знаю датапаки: что они добавляют в мир — биомы, структуры, предметы.
4. Знаю кастомные плагины: «Разлом», «Свадьба», «Скорбь» — механики, команды, крафты.
5. Крафты Minecraft: рецепт и схема сетки (включая крафты Разлома).
6. Механики Minecraft: руды и высоты, мобы, зачарования, Незер, Край, фермы, редстоун, зелья.
7. Отвечаю на любом языке, на котором ко мне обратятся.
8. Принимаю заявку на помощь: кнопка «Нужна помощь» или /zayavka — заявка уходит
   администрации, ответ приходит сюда же в личку.

Команды:
/status — онлайн и версия сервера
/info — вся информация о сервере
/welcome — приветственная карточка сервера
/rules — правила
/plugins — плагины, которыми пользуются игроки, и их команды
/datapacks — датапаки сервера
/plugins разлом — подробно про плагин по названию
/craft <предмет> — рецепт, например /craft поршень или /craft руна разлома
/song <название> — найти песню и прислать файлом
/web <запрос> — поиск в интернете
/video <тема> — найти видео
/menu — меню с кнопками
/help — эта справка
/zayavka — оставить заявку на помощь (или кнопка «Нужна помощь»)
/moi — статус моих заявок
/reset — очистить историю диалога

Финансовая поддержка сервера обсуждается только в ЛС @belyak345 — звёздами либо
в какой-нибудь другой валюте.

Для администрации: /console <команда> — консоль сервера прямо из чата,
/eva — управление античитом Eva (check/report/toggle/setban/reload/bot test),
/rcon — настройка связи с сервером, /wl add <ник> — добавить в вайтлист,
/moder on|off — фильтр оскорблений, /addbad <слово> — своё слово в фильтр,
/setinfo — карточка сервера, /setip <адрес:порт> — адреса для проверки статуса,
/kb reload — перечитать знания из папки knowledge,
/рассылка — рассылка сообщений по чатам с панелью настройки (только владелец).

В группе вызывай меня словом «%s» или упоминанием @%s.""" % (config.BOT_NAME, config.BOT_USERNAME)


TICKET_STATUS_RU = {
    "new": "новая",
    "taken": "в работе",
    "answered": "есть ответ",
    "accepted": "принята",
    "rejected": "отклонена",
    "closed": "закрыта",
}


class Bot:
    def __init__(self):
        self.tg = Tg(config.BOT_TOKEN)
        self.db = Store()
        self.state = {}                       # user_id -> черновик формы
        self.cooldown = {}                    # (chat_id, user_id) -> время последнего вопроса
        self.warned = {}                      # (chat_id, user_id) -> время последнего предупреждения
        self.insult_ts = {}                   # chat_id -> время последнего отчёта о нарушении
        self.group_count = {}                 # chat_id -> [окно, счётчик]
        self.snap_lock = threading.RLock()
        self.snap_text = ""
        self.snap_ts = 0
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.music_slots = threading.BoundedSemaphore(2)   # не больше двух песен одновременно
        self.seen_updates = set()
        self.ai_cache = {}          # (чат, текст) -> время проверки
        self.ai_budget = {}         # чат -> [окно, счётчик] для ИИ-проверок
        self.answers = {}           # нормализованный вопрос -> (время, ответ)
        self.answers_lock = threading.Lock()
        self.dm_budget = {}         # user_id -> [окно минуты, счётчик, окно суток, счётчик]
        self.insult_global = [0.0, 0]   # [окно часа, счётчик] на все чаты вместе
        self.last_poll = time.time()    # когда поллинг последний раз отвечал
        self._rcon_state = None         # последний результат проверки консоли
        self._rcon_tick = 0
        moder.reload(self.db.bad_words())
        self.me = self.tg.get_me()
        self.register_commands()
        log.info("вошёл как @%s", self.me.get("username"))
        log.info("знания о сервере: записей %d (%s)", kb.count(),
                 ", ".join(kb.sources()) or "нет")
        try:
            ready = ["%s=%s" % (name, "да" if ok else "нет")
                     for name, ok, _info in rcon.channels(self.db)]
            log.info("каналы консоли: %s", ", ".join(ready))
        except Exception as exc:  # noqa: BLE001
            log.info("каналы консоли: проверить не удалось (%s)", exc)

    def register_commands(self):
        """Меню команд в Telegram (без русских названий — их API не принимает)."""
        base = [
            ("menu", "меню с кнопками"),
            ("help", "справка по командам"),
            ("status", "онлайн и версия сервера"),
            ("info", "информация о сервере"),
            ("welcome", "приветственная карточка сервера"),
            ("rules", "правила сервера"),
            ("craft", "рецепт крафта, например /craft поршень"),
            ("plugins", "плагины сервера и их команды"),
            ("datapacks", "датапаки сервера"),
            ("song", "найти песню и прислать файлом"),
            ("web", "поиск в интернете"),
            ("video", "найти видео по теме"),
            ("zayavka", "заявка на помощь администрации"),
            ("moi", "статус моих заявок"),
            ("feedback", "написать администрации"),
            ("reset", "очистить историю диалога"),
        ]
        self.tg.set_my_commands(base)
        # владельцу и доверенному помощнику показываем ещё рассылку и заявки
        extra = [("rassylka", "рассылка по чатам"),
                 ("helpq", "открытые заявки на помощь")]
        for admin_id in config.BROADCAST_ADMINS + config.HELP_ADMINS:
            self.tg.set_my_commands(base + extra, chat_id=admin_id)

    # ------------------------------------------------------------------
    # цикл опроса
    # ------------------------------------------------------------------
    def heartbeat(self):
        """Отметка «бот жив». Сторож смотрит на этот файл: если он не
        обновлялся дольше 5 минут — процесс завис и его надо перезапустить."""
        try:
            os.makedirs(config.DATA_DIR, exist_ok=True)
            with open(os.path.join(config.DATA_DIR, "heartbeat"), "w",
                      encoding="utf-8") as fh:
                fh.write(str(int(time.time())))
        except Exception:
            pass

    def run(self):
        threading.Thread(target=self._refresh_loop, daemon=True).start()
        threading.Thread(target=self._watchdog_loop, daemon=True).start()
        offset = 0
        log.info("поллинг запущен")
        while True:
            updates = self.tg.get_updates(offset, timeout=40)
            self.last_poll = time.time()
            self.heartbeat()
            for upd in updates:
                try:
                    offset = max(offset, upd.get("update_id", 0) + 1)
                    uid = upd.get("update_id")
                    if uid in self.seen_updates:
                        continue
                    self.seen_updates.add(uid)
                    if len(self.seen_updates) > 5000:
                        self.seen_updates = set(list(self.seen_updates)[-2000:])
                    self.pool.submit(self._safe_handle, upd)
                except Exception as exc:
                    log.exception("ошибка разбора апдейта: %s", exc)

    def _watchdog_loop(self):
        """Сам себя перезапускаю, если поллинг завис.

        Если процесс жив, но не получает и не обрабатывает сообщения дольше
        config.POLL_STUCK_SEC — выходим с кодом 1: systemd, cron-сторож или
        deploy/panel-start.sh поднимут бота заново.
        На Windows ещё и не даём компьютеру уснуть, пока Ева работает.
        """
        awake_ts = 0.0
        while True:
            time.sleep(30)
            age = time.time() - self.last_poll
            if age > config.POLL_STUCK_SEC:
                log.error("поллинг завис на %d сек — перезапускаю процесс", int(age))
                os._exit(1)
            if config.KEEP_AWAKE and os.name == "nt" and time.time() - awake_ts > 50:
                awake_ts = time.time()
                try:
                    import ctypes
                    # ES_CONTINUOUS | ES_SYSTEM_REQUIRED: система не засыпает,
                    # экран при этом может гаснуть как обычно.
                    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
                except Exception:
                    pass
            # Раз в 5 минут проверяем консоль сервера и пишем в лог только
            # изменения: удобно увидеть, когда RCON появился/пропал.
            self._rcon_tick += 1
            if self._rcon_tick % 10 == 0:
                try:
                    ok, out = rcon.test(self.db, timeout=5)
                except Exception as exc:  # noqa: BLE001
                    ok, out = False, str(exc)
                if ok != self._rcon_state:
                    self._rcon_state = ok
                    text = " ".join(str(out or "").split())[:200]
                    if ok:
                        log.info("RCON: связь есть — %s", text)
                    else:
                        log.info("RCON: связи нет — %s", text)

    def _safe_handle(self, upd):
        try:
            if "message" in upd or "edited_message" in upd:
                self.on_message(upd.get("message") or upd.get("edited_message"))
            elif "callback_query" in upd:
                self.on_callback(upd["callback_query"])
            elif "channel_post" in upd:
                self.on_message(upd["channel_post"], is_channel=True)
        except Exception as exc:
            log.exception("ошибка обработки: %s", exc)
            try:
                chat = (upd.get("message") or {}).get("chat") or {}
                if chat.get("type") == "private":
                    self.tg.send(chat.get("id"), "Внутренняя ошибка при обработке. "
                                                 "Попробуй ещё раз или напиши /menu.")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # сервер
    # ------------------------------------------------------------------
    def server_text(self, force=False):
        with self.snap_lock:
            if not force and self.snap_text and (time.time() - self.snap_ts) < 150:
                return self.snap_text
        addrs = self.db.server_addrs()
        if not addrs:
            text = "Адрес сервера ещё не задан администратором."
        else:
            snap = mc.best_snapshot(addrs)
            bed = mc.bedrock_snapshot(self.db.bedrock_addr())
            text = mc.full_status_text(snap, bed)
        with self.snap_lock:
            self.snap_text = text
            self.snap_ts = time.time()
        return text

    def _refresh_loop(self):
        while True:
            try:
                if self.db.server_addrs():
                    self.server_text(force=True)
            except Exception as exc:
                log.info("обновление статуса: %s", exc)
            time.sleep(max(60, config.STATUS_INTERVAL))

    # ------------------------------------------------------------------
    # сообщения
    # ------------------------------------------------------------------
    def on_message(self, msg, is_channel=False):
        if not msg:
            return
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        chat_type = chat.get("type", "private")
        user = msg.get("from") or {}
        is_group = chat_type in ("group", "supergroup")
        text = (msg.get("text") or msg.get("caption") or "").strip()

        if user.get("is_bot"):
            return
        self.db.touch_user(user, chat_id, is_admin=self.db.is_admin(user.get("id"), user.get("username")))
        self.db.touch_chat(chat, user)

        if is_group or is_channel:
            is_cmd = text.startswith("/")
            insult = False
            if text:
                insult = self.moderate(msg, chat_id, chat.get("title", ""), user, text) if not is_cmd else False
            if text and not insult:
                self.db.add_knowledge(chat_id, chat.get("title", ""), user, text)
            if not text:
                return
            if is_cmd:
                if self.admin_message(msg, text, chat_id, user):
                    return
                if self.command(msg, text, chat_id, user, group=True):
                    return
            woken = self._woken(msg, text)
            if insult and not woken:
                return
            if not woken:
                return
            allowed, wait = self._group_rate(chat_id, user.get("id"))
            if not allowed:
                self._warn_wait(chat_id, user, wait, msg.get("message_id"))
                return
            question = self._strip_wake(text)
            if not question:
                question = "что ты умеешь"
            query, sure = music.is_request(question)
            if query and self.music_handle(query, chat_id, user,
                                           reply_to=msg.get("message_id"), sure=sure):
                return
            answer, delivered = self.answer(question, chat_id, user, group=True,
                                            reply_to=msg.get("message_id"))
            if not delivered:
                sent = self.deliver(chat_id, answer, reply_to=msg.get("message_id"),
                                    group=True)
                if sent:
                    self.db.link(chat_id, sent.get("message_id"), "bot")
            return

        # личка (текст или вложение — скриншот к заявке тоже принимаем)
        has_file = any(msg.get(k) for k in ("photo", "document", "video", "animation"))
        if not text and not has_file:
            return
        self.handle_private(msg, text, user)

    def _woken(self, msg, text):
        if msg.get("reply_to_message") and msg["reply_to_message"].get("from", {}).get("id") == self.me.get("id"):
            return True
        username = (self.me.get("username") or config.BOT_USERNAME).lower()
        if ("@" + username) in text.lower():
            return True
        for ent in (msg.get("entities") or []) + (msg.get("caption_entities") or []):
            if ent.get("type") == "text_mention":
                person = ent.get("user") or {}
                if person.get("id") == self.me.get("id"):
                    return True
            if ent.get("type") == "mention":
                fragment = text[ent.get("offset", 0):ent.get("offset", 0) + ent.get("length", 0)]
                if fragment.lower() == "@" + username:
                    return True
        if WAKE_RE.search(text):
            return True
        return False

    def _strip_wake(self, text):
        username = (self.me.get("username") or config.BOT_USERNAME)
        question = text.replace("@" + username, " ")
        question = WAKE_RE.sub(" ", question, count=1)
        question = re.sub(r"^[\s,.:;!?\-]+", "", question)
        return " ".join(question.split()).strip()

    # ------------------------------------------------------------------
    # кулдаун
    # ------------------------------------------------------------------
    def _group_rate(self, chat_id, user_id):
        """Возвращает (можно_отвечать, сколько_секунд_ждать)."""
        now = time.time()
        key = (chat_id, user_id)
        cd = self.db.cooldown_sec()
        last = self.cooldown.get(key, 0)
        if cd and now - last < cd:
            return False, max(1, int(cd - (now - last)) + 1)
        self.cooldown[key] = now
        window = self.group_count.setdefault(chat_id, [now, 0])
        if now - window[0] > 3600:
            window[0], window[1] = now, 0
        window[1] += 1
        if window[1] > config.GROUP_DAILY_CAP:
            log.warning("лимит ответов в чате %s исчерпан", chat_id)
            return False, 0
        return True, 0

    def _warn_wait(self, chat_id, user, wait, reply_to=None):
        if wait <= 0:
            return
        key = (chat_id, user.get("id"))
        now = time.time()
        if now - self.warned.get(key, 0) < config.GROUP_WARN_GAP:
            return
        self.warned[key] = now
        self.tg.send(chat_id, "%s, подожди ещё %d сек. — потом спрошу сколько нужно."
                     % (user.get("first_name") or "Друг", wait), reply_to=reply_to)

    # ------------------------------------------------------------------
    # модерация: оскорбления
    # ------------------------------------------------------------------
    def moderate(self, msg, chat_id, chat_title, user, text):
        """Первая линия: словарь оскорблений. True — сообщение нарушение."""
        if self.db.flag("moder", "on") != "on":
            return False
        reply = msg.get("reply_to_message") or {}
        ents = (msg.get("entities") or []) + (msg.get("caption_entities") or [])
        clean = moder.strip_links(text, ents)   # ссылки нарушением не считаем
        address = [e for e in ents if e.get("type") in ("mention", "text_mention")]
        directed = bool(reply) or ("@" in clean) or bool(address)
        try:
            names = self.db.chat_names(chat_id)
        except Exception:
            names = []
        found = moder.detect_full(text, directed=directed, entities=ents, names=names)
        if not found:
            self.ai_watch(msg, chat_id, chat_title, user, text)   # вторая линия
            return False
        target = ""
        if reply.get("from") and not reply["from"].get("is_bot"):
            target = mention(reply["from"])
        self.report_violation(chat_id, chat_title, user, text, found["word"],
                              found["category"], target, msg=msg,
                              lang=found.get("lang"), method=found.get("method"))
        return True

    def report_violation(self, chat_id, chat_title, user, text, word, category,
                         target="", msg=None, lang="", method=""):
        """Запись в историю и карточка админам в личку.

        В общем чате бот молчит: ни карточки, ни отметок администрации.
        """
        self.db.add_violation(chat_id, chat_title, user, strip_tags(target), word,
                              text, lang=lang, method=method)
        now = time.time()
        if now - self.insult_ts.get(chat_id, 0) < config.INSULT_REPORT_GAP:
            return
        self.insult_ts[chat_id] = now
        if self.db.flag("insultdm", "on") != "on":
            return

        who = mention(user) or "игрок"
        lines = ["<b>Нарушение правил</b>"]
        lines.append("Чат: %s" % esc(chat_title or str(chat_id)))
        lines.append("Кто: %s" % who)
        if target:
            lines.append("Кому: %s" % target)
        lines.append("Тип: %s" % esc(category))
        lines.append("Слово: %s" % esc(word))
        if lang:
            lines.append("Язык: %s" % esc(moder.lang_name(lang)))
        if method:
            lines.append("Как поймано: %s" % esc(method))
        lines.append("Сообщение: «%s»" % esc(text[:300]))
        lines.append("Правила: %s" % esc(sinfo.get(self.db, "rules")))
        card = "\n".join(lines)

        for uid in self.db.admin_ids():
            try:
                self.tg.send_html(uid, card)
            except Exception as exc:
                log.info("не смог отправить карточку нарушения %s: %s", uid, exc)

    # ------------------------------------------------------------------
    # вторая линия: словарь не нашёл — спрашиваем модель
    # ------------------------------------------------------------------
    def _maybe_insult(self, msg, text):
        """Дешёвая проверка перед платным запросом к модели."""
        if AI_HINT_RE.search(text) or ADDRESS_RE.search(text):
            return True
        reply = (msg or {}).get("reply_to_message") or {}
        author = reply.get("from") or {}
        return bool(author and not author.get("is_bot"))

    def ai_watch(self, msg, chat_id, chat_title, user, text):
        if self.db.flag("moder_ai", "on") != "on":
            return
        if not text or len(text) < 3 or len(text) > 300:
            return
        if not re.search(r"[^\W\d_]", text):
            return
        if not self._maybe_insult(msg, text):
            return
        now = time.time()
        key = (chat_id, text.strip().lower()[:200])
        if now - self.ai_cache.get(key, 0) < 3600:
            return
        if len(self.ai_cache) > 1200:
            self.ai_cache.clear()
        self.ai_cache[key] = now
        window = self.ai_budget.setdefault(chat_id, [now, 0])
        if now - window[0] > 60:
            window[0], window[1] = now, 0
        if window[1] >= config.INSULT_AI_PER_CHAT_MIN:
            return
        window[1] += 1
        if now - self.insult_global[0] > 3600:
            self.insult_global[0], self.insult_global[1] = now, 0
        if self.insult_global[1] >= config.INSULT_AI_GLOBAL_HOUR:
            return
        self.insult_global[1] += 1
        self.pool.submit(self._ai_check, msg, chat_id, chat_title, dict(user), text)

    def _ai_check(self, msg, chat_id, chat_title, user, text):
        try:
            found = ai.classify_insult(text)
        except Exception as exc:
            log.info("ИИ-проверка упала: %s", exc)
            return
        if not found or not found.get("insult"):
            return
        if self.db.flag("moder", "on") != "on":
            return
        reply = (msg or {}).get("reply_to_message") or {}
        target = ""
        if reply.get("from") and not reply["from"].get("is_bot"):
            target = mention(reply["from"])
        self.report_violation(chat_id, chat_title, user, text,
                              found.get("word") or "оскорбление",
                              moder.CAT_INSULT, target, msg=msg,
                              method="проверка ИИ")

    # ------------------------------------------------------------------
    # личка
    # ------------------------------------------------------------------
    def handle_private(self, msg, text, user):
        chat_id = msg["chat"]["id"]
        uid = user.get("id")
        state = self.state.get(uid)

        # панель рассылки ждёт текст / фото / кнопку от владельца
        if broadcast.capture(self, msg, text, user):
            return

        if state and text.lower() in ("отмена", "/cancel", "назад", "стоп"):
            self.state.pop(uid, None)
            self.tg.send(chat_id, "Отменено.", keyboard=MAIN_MENU)
            return
        if state:
            self.form_step(msg, state, text, chat_id, user)
            return

        if self.admin_message(msg, text, chat_id, user):
            return

        if text.startswith("/"):
            if self.command(msg, text, chat_id, user):
                return
            cmd = text.split()[0].split("@")[0]
            self.tg.send(chat_id, "Не знаю такую команду: %s\nСписок команд: /help"
                         % cmd[:40])
            return

        query, sure = music.is_request(text)
        if query and self.music_handle(query, chat_id, user, keyboard=MAIN_MENU, sure=sure):
            self.db.remember(chat_id, "user", text)
            return

        self.db.remember(chat_id, "user", text)
        answer, delivered = self.answer(text, chat_id, user, group=False, keyboard=MAIN_MENU)
        self.db.remember(chat_id, "assistant", answer)
        if not delivered:
            self.deliver(chat_id, answer, keyboard=MAIN_MENU)

    # ------------------------------------------------------------------
    # команды
    # ------------------------------------------------------------------
    def command(self, msg, text, chat_id, user, group=False):
        uid = user.get("id")
        is_admin = self.db.is_admin(uid, user.get("username"))
        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        arg = " ".join(parts[1:]).strip()
        menu = None if group else MAIN_MENU

        if cmd in ("/start", "/menu", "/меню"):
            hello = ("Я %s, ИИ-помощник сервера %s. Отвечаю фактами, без воды.\n"
                     "Выбери раздел кнопкой или просто напиши вопрос."
                     % (config.BOT_NAME, sinfo.get(self.db, "about")))
            self.tg.send(chat_id, hello, keyboard=menu or MAIN_MENU)
            return True
        if cmd in ("/help", "/помощь"):
            self.tg.send(chat_id, HELP_TEXT, keyboard=BACK_MENU)
            return True
        if cmd in ("/plugins", "/плагины", "/plugin"):
            if arg:
                self.deliver(chat_id, self.knowledge_answer(arg, "plugins"),
                             keyboard=BACK_MENU, group=group)
            else:
                self.tg.send(chat_id, self.plugins_text(), keyboard=BACK_MENU)
            return True
        if cmd in ("/datapacks", "/датапаки", "/datapack", "/дп"):
            if arg:
                self.deliver(chat_id, self.knowledge_answer(arg, "datapacks"),
                             keyboard=BACK_MENU, group=group)
            else:
                self.tg.send(chat_id, self.datapacks_text(), keyboard=BACK_MENU)
            return True
        if cmd in ("/info", "/about", "/сервер", "/инфа", "/инфо"):
            self.tg.send(chat_id, sinfo.render(self.db), keyboard=BACK_MENU)
            return True
        if cmd in ("/welcome", "/приветствие"):
            self.tg.send(chat_id, sinfo.welcome(self.db), keyboard=BACK_MENU)
            return True
        if cmd in ("/rules", "/правила"):
            self.tg.send(chat_id, "Правила сервера: %s\n\nВход: %s"
                         % (sinfo.get(self.db, "rules"), sinfo.get(self.db, "whitelist")),
                         keyboard=BACK_MENU)
            return True
        if cmd in ("/status", "/статус"):
            self.deliver(chat_id, self.server_text(force=True), keyboard=BACK_MENU,
                         group=group)
            return True
        if cmd == "/craft":
            if not arg:
                self.tg.send(chat_id, "Напиши: /craft поршень", keyboard=BACK_MENU)
                return True
            self.deliver(chat_id, self.craft_answer(arg), keyboard=BACK_MENU, group=group)
            return True
        if cmd == "/video":
            self.tg.send(chat_id, self.video_answer(arg or "Minecraft гайд"), keyboard=BACK_MENU)
            return True
        if cmd == "/web":
            if not arg:
                self.tg.send(chat_id, "Напиши: /web как сделать ферму опыта", keyboard=BACK_MENU)
                return True
            self.deliver(chat_id, self.web_answer(arg), keyboard=BACK_MENU, group=group)
            return True
        if cmd in ("/song", "/песня", "/песню", "/музыка", "/music", "/трек"):
            if cmd in ("/music",) and arg.lower() in ("diag", "диаг", "тест", "test"):
                if not is_admin:
                    self.tg.send(chat_id, "Эта команда только для администрации сервера.")
                    return True
                self.music_diag(chat_id)
                return True
            if not arg:
                self.tg.send(chat_id, "Напиши так: /song исполнитель — название песни.\n"
                                      "Например: /song Alex C. Angel of Darkness",
                             keyboard=menu)
                return True
            self.music_handle(arg, chat_id, user, keyboard=menu)
            return True
        if cmd == "/id":
            self.tg.send(chat_id, "Твой ID: %s" % uid, keyboard=BACK_MENU)
            return True
        if cmd == "/reset":
            self.db.clear_history(chat_id)
            self.tg.send(chat_id, "История диалога очищена.", keyboard=BACK_MENU)
            return True
        if cmd in ("/feedback", "/zayavka", "/заявка", "/help_request", "/нужнапомощь"):
            if self._private_ok(msg, user, chat_id):
                self.start_help(chat_id, uid)
            return True
        if cmd in ("/moi", "/my", "/мои", "/zayavki", "/заявки"):
            self.tg.send(chat_id, self.my_tickets_text(uid), keyboard=self.my_tickets_kb(uid))
            return True

        # какую версию кода реально крутит сервер + какие кнопки сейчас в меню
        if cmd in ("/version", "/версия"):
            if not is_admin:
                self.tg.send(chat_id, "Эта команда только для администрации сервера.")
                return True
            self.tg.send(chat_id,
                         "Версия кода: %s\nКнопки меню (%d): %s\nФайл: %s"
                         % (CODE_VERSION, len(MENU_TITLES), " | ".join(MENU_TITLES),
                            __file__),
                         keyboard=MAIN_MENU)
            return True

        # рассылка по чатам (только владелец и доверенный id)
        if broadcast.is_command(cmd):
            broadcast.handle_command(self, msg, text, chat_id, user, cmd)
            return True

        if cmd in HELP_CMDS and self._is_helper(uid, user.get("username")):
            return self.help_command(msg, text, chat_id, user, cmd, arg)

        if not is_admin:
            if cmd in ADMIN_ONLY:
                self.tg.send(chat_id, "Эта команда только для администрации сервера.")
                return True
            return False

        if cmd in ("/kb", "/знания"):
            sub = arg.lower().strip()
            if sub in ("reload", "перечитать", "обновить", ""):
                total = kb.reload()
                kb_block = ("Знания: %d записей.\nФайлы: %s"
                            % (total, ", ".join(kb.sources()) or "нет"))
                if sub:
                    self.tg.send(chat_id, "Перечитал знания о сервере.\n" + kb_block)
                    return True
                self.tg.send(chat_id, kb_block + "\n\nПеречитать: /kb reload")
                return True
            entry = kb.get(arg)
            if not entry:
                self.tg.send(chat_id, "В знаниях нет «%s». /kb — что есть." % arg)
                return True
            self.tg.send(chat_id, entry.render(), keyboard=BACK_MENU)
            return True

        if cmd in ("/admin", "/stats", "/админ"):
            st = self.db.stats()
            self.tg.send(chat_id, "Админ-панель\nПользователей: %d\nОбращений: %d (открытых %d)\n"
                                  "Знаний из группы: %d\nФактов: %d\nНарушений: %d\n"
                                  "Адреса сервера: %s\nКулдаун в группе: %d сек.\n"
                                  "Эмоции: %s / эмодзи: %s"
                         % (st["users"], st["tickets"], st["open"], st["knowledge"],
                            st["facts"], st["violations"],
                            ", ".join(self.db.server_addrs()) or "не задан",
                            self.db.cooldown_sec(),
                            self.db.flag("emotions", "on"), self.db.flag("emoji", "off")),
                         keyboard=keyboard([[("Открытые заявки", "a:tickets")],
                                            [("Статус сервера", "m:status")],
                                            [("О сервере", "m:about")],
                                            [("В меню", "m:menu")]]))
            return True
        if cmd in ("/setinfo", "/настройка"):
            if not arg:
                self.tg.send(chat_id, sinfo.edit_list(self.db))
                return True
            key = arg.split()[0].lower()
            value = arg[len(key):].strip()
            if key not in sinfo.KEYS:
                self.tg.send(chat_id, "Нет такого поля: %s\n\n%s" % (key, sinfo.edit_list(self.db)))
                return True
            if not value:
                self.tg.send(chat_id, "%s = %s\n\nЧтобы изменить: /setinfo %s <новое значение>"
                             % (key, sinfo.get(self.db, key), key))
                return True
            sinfo.set_field(self.db, key, value)
            self.tg.send(chat_id, "Сохранено: %s = %s" % (key, value))
            return True
        if cmd == "/setip":
            if not arg:
                self.tg.send(chat_id, "Напиши адреса для проверки статуса: "
                                      "/setip 107.161.154.218:25926")
                return True
            addrs = mc.split_addrs(arg)
            if not addrs:
                self.tg.send(chat_id, "Не понял адрес. Пример: /setip play.example.com:25565")
                return True
            self.db.set("server_addr", ", ".join(addrs))
            self.server_text(force=True)
            self.tg.send(chat_id, "Адреса сохранены: %s" % ", ".join(addrs))
            return True
        if cmd == "/setbedrock":
            if not arg:
                self.tg.send(chat_id, "Текущий Bedrock: %s\nЧтобы изменить: /setbedrock 1.2.3.4:19132"
                             % self.db.bedrock_addr())
                return True
            self.db.set("bedrock_addr", arg)
            self.server_text(force=True)
            self.tg.send(chat_id, "Bedrock-адрес сохранён: %s" % arg)
            return True
        if cmd == "/ip":
            self.tg.send(chat_id, "Статус проверяется по: %s\nBedrock: %s"
                         % (", ".join(self.db.server_addrs()) or "не задан", self.db.bedrock_addr()))
            return True
        if cmd in ("/admins", "/админы"):
            self.tg.send(chat_id, "Администраторы (получают отметку при нарушениях):\n%s\n\n"
                                  "Добавить: /addadmin @username или /addadmin <id>\n"
                                  "Убрать: /deladmin @username" % ", ".join(self.db.admins()))
            return True
        if cmd == "/addadmin":
            if not arg:
                self.tg.send(chat_id, "Напиши: /addadmin @username или /addadmin 123456789")
                return True
            who = arg.split()[0]
            if who.isdigit() or who.startswith("@"):
                if self.db.add_admin(who):
                    self.tg.send(chat_id, "Добавлен: %s\nСписок: %s" % (who, ", ".join(self.db.admins())))
                else:
                    self.tg.send(chat_id, "%s уже в списке." % who)
            else:
                self.tg.send(chat_id, "Нужен @username или числовой id.")
            return True
        if cmd == "/deladmin":
            if not arg:
                self.tg.send(chat_id, "Напиши: /deladmin @username")
                return True
            who = arg.split()[0]
            self.db.del_admin(who)
            self.tg.send(chat_id, "Убран: %s\nСписок: %s" % (who, ", ".join(self.db.admins())))
            return True
        if cmd == "/cooldown":
            if not arg.isdigit():
                self.tg.send(chat_id, "Сейчас кулдаун: %d сек.\nИзменить: /cooldown 15"
                             % self.db.cooldown_sec())
                return True
            self.db.set("cooldown", int(arg))
            self.tg.send(chat_id, "Кулдаун в группе: %d сек." % int(arg))
            return True
        if cmd in ("/emotions", "/эмоции"):
            val = arg.lower() if arg else ("off" if self.db.flag("emotions", "on") == "on" else "on")
            self.db.set("emotions", "on" if val in ("on", "вкл", "1", "да") else "off")
            self.tg.send(chat_id, "Эмоции: %s" % self.db.flag("emotions", "on"))
            return True
        if cmd in ("/emoji", "/эмодзи"):
            val = arg.lower() if arg else ("off" if self.db.flag("emoji", "off") == "on" else "on")
            self.db.set("emoji", "on" if val in ("on", "вкл", "1", "да") else "off")
            self.tg.send(chat_id, "Эмодзи: %s" % self.db.flag("emoji", "off"))
            return True
        if cmd == "/insultdm":
            val = arg.lower() if arg else ("off" if self.db.flag("insultdm", "on") == "on" else "on")
            self.db.set("insultdm", "on" if val in ("on", "вкл", "1", "да") else "off")
            self.tg.send(chat_id, "Карточки о нарушениях приходят только в личку админам: %s"
                         % self.db.flag("insultdm", "on"))
            return True
        if cmd == "/violations":
            rows = self.db.violations(10)
            if not rows:
                self.tg.send(chat_id, "Нарушений пока нет.")
                return True
            lines = ["Последние нарушения:"]
            for r in rows:
                lines.append("#%d %s — %s (%s) %s" % (
                    r["id"], r["username"] or r["user_id"], r["word"],
                    time.strftime("%d.%m %H:%M", time.localtime(r["created_at"])),
                    (r["text"] or "")[:80]))
            self.tg.send(chat_id, "\n".join(lines))
            return True
        if cmd == "/fact":
            if arg:
                fid = self.db.add_fact(arg)
                self.tg.send(chat_id, "Факт #%d добавлен. Бот будет использовать его в ответах." % fid)
            return True
        if cmd == "/facts":
            rows = self.db.facts(30)
            if not rows:
                self.tg.send(chat_id, "Фактов пока нет. Добавь: /fact <текст>")
                return True
            body = "\n".join("#%d %s" % (r["id"], r["text"][:160]) for r in rows)
            self.tg.send(chat_id, "Факты:\n" + body)
            return True
        if cmd == "/delfact":
            if arg.isdigit():
                self.db.del_fact(int(arg))
                self.tg.send(chat_id, "Факт #%s удалён." % arg)
            return True
        if cmd in ("/tickets", "/zayavki"):
            rows = self.db.recent_tickets(10)
            if not rows:
                self.tg.send(chat_id, "Заявок пока нет.")
                return True
            lines = []
            for r in rows:
                lines.append("#%d [%s] %s — %s (%s)" % (
                    r["id"], r["kind"], r["username"] or r["user_id"],
                    (r["body"] or "").replace("\n", " ")[:90], r["status"]))
            self.tg.send(chat_id, "Последние заявки:\n" + "\n".join(lines))
            return True
        if cmd == "/answer":
            if not arg or not arg.split()[0].isdigit():
                self.tg.send(chat_id, "Напиши: /answer 12 текст ответа")
                return True
            tid = int(arg.split()[0])
            body = arg[len(str(tid)):].strip()
            ticket = self.db.ticket(tid)
            if not ticket:
                self.tg.send(chat_id, "Заявка #%d не найдена." % tid)
                return True
            if not body:
                self.tg.send(chat_id, "Пустой ответ.")
                return True
            self.reply_to_user(ticket, body)
            self.tg.send(chat_id, "Ответ по заявке #%d отправлен." % tid)
            return True
        if cmd in ("/console", "/cmd", "/консоль"):
            if not arg:
                self.tg.send(chat_id, "Консоль сервера прямо из чата. Пиши:\n"
                                      "/console list\n/console whitelist add Nick\n"
                                      "/console time set day\n\n"
                                      "Одна команда за раз, без слэша в начале.\n"
                                      "Каналы и их настройка: /rcon")
                return True
            with Typing(self.tg, chat_id):
                ok, out, channel = rcon.run_any(self.db, arg)
            head = "Команда выполнена" + (" (%s):" % channel if channel else ":")
            if not ok:
                head = "Не получилось:"
                lines = [head, "> %s" % arg, out[:2000]]
                lines.append("")
                lines.append("Каналы консоли:")
                for name, ready_now, info in rcon.channels(self.db):
                    lines.append("%s %s — %s" % ("✅" if ready_now else "❌", name, info))
                self.tg.send(chat_id, "\n".join(lines))
                return True
            self.tg.send(chat_id, "%s\n> %s\n\n%s" % (head, arg, out[:3000]))
            return True
        if cmd == "/rcon":
            conf = rcon.cfg(self.db)
            sub = arg.split()[0].lower() if arg else ""
            rest = arg.split()[1:] if arg else []
            if sub == "set" and len(rest) >= 3:
                self.db.set("rcon_host", rest[0])
                self.db.set("rcon_port", rest[1])
                self.db.set("rcon_pass", " ".join(rest[2:]))
                self.db.set("rcon_on", "on")
                try:
                    self.tg.delete(chat_id, msg.get("message_id"))
                except Exception:
                    pass
                ok, out = rcon.test(self.db)
                self.tg.send(chat_id, "RCON сохранён: %s:%s\nСвязь: %s\n%s"
                             % (rest[0], rest[1], "есть" if ok else "нет", out[:400]))
                return True
            if sub == "host" and rest:
                self.db.set("rcon_host", rest[0])
                self.tg.send(chat_id, "Адрес RCON: %s" % rest[0])
                return True
            if sub == "port" and rest:
                self.db.set("rcon_port", rest[0])
                self.tg.send(chat_id, "Порт RCON: %s" % rest[0])
                return True
            if sub == "pass" and rest:
                self.db.set("rcon_pass", " ".join(rest))
                self.db.set("rcon_on", "on")
                try:
                    self.tg.delete(chat_id, msg.get("message_id"))
                except Exception:
                    pass
                self.tg.send(chat_id, "Пароль RCON сохранён. Сообщение с паролем я удалил.")
                return True
            if sub in ("wl", "template") and rest:
                self.db.set("wl_template", " ".join(rest))
                self.tg.send(chat_id, "Команда добавления в вайтлист: %s" % " ".join(rest))
                return True
            if sub in ("unwl", "untemplate") and rest:
                self.db.set("wl_untemplate", " ".join(rest))
                self.tg.send(chat_id, "Команда удаления из вайтлиста: %s" % " ".join(rest))
                return True
            if sub in ("on", "off", "вкл", "выкл"):
                self.db.set("rcon_on", "on" if sub in ("on", "вкл") else "off")
                self.tg.send(chat_id, "RCON: %s" % self.db.flag("rcon_on", "on"))
                return True
            if sub in ("test", "проверка", "тест"):
                with Typing(self.tg, chat_id):
                    ok, out = rcon.test(self.db)
                self.tg.send(chat_id, "Проверка RCON: %s\n%s"
                             % ("связь есть" if ok else "нет связи", out[:600]))
                return True
            if sub in ("agent", "агент") and rest:
                if rest[0].startswith("http") and len(rest) >= 2:
                    self.db.set("agent_url", rest[0].rstrip("/"))
                    self.db.set("agent_token", rest[1])
                    self.db.set("agent_on", "on")
                    ok, out = rcon.agent_run(self.db, "#status")
                    self.tg.send(chat_id, "Агент (HTTP) сохранён: %s\nСвязь: %s\n%s"
                                 % (rest[0], "есть" if ok else "нет", out[:400]))
                    return True
                if rest[0] in ("file", "файл") and len(rest) >= 2:
                    self.db.set("agent_dir", " ".join(rest[1:]))
                    self.db.set("agent_on", "on")
                    ok, out = rcon.agent_run(self.db, "#status")
                    self.tg.send(chat_id, "Файловый канал агента: %s\nСвязь: %s\n%s"
                                 % (" ".join(rest[1:]), "есть" if ok else "нет", out[:400]))
                    return True
                if len(rest) >= 3 and rest[1].isdigit():
                    self.db.set("agent_host", rest[0])
                    self.db.set("agent_port", int(rest[1]))
                    self.db.set("agent_token", " ".join(rest[2:]))
                    self.db.set("agent_on", "on")
                    try:
                        self.tg.delete(chat_id, msg.get("message_id"))
                    except Exception:
                        pass
                    ok, out = rcon.agent_run(self.db, "#status")
                    self.tg.send(chat_id, "Агент: %s:%s\nСвязь: %s\n%s"
                                 % (rest[0], rest[1], "есть" if ok else "нет", out[:400]))
                    return True
                self.db.set("agent_on", "on" if rest[0] in ("on", "вкл") else "off")
                self.tg.send(chat_id, "Канал агента: %s" % self.db.flag("agent_on", "on"))
                return True
            if sub in ("panel", "панель") and rest:
                if rest[0].startswith("http") and len(rest) >= 3:
                    self.db.set("panel_url", rest[0].rstrip("/"))
                    self.db.set("panel_key", rest[1])
                    self.db.set("panel_server", rest[2])
                    self.db.set("panel_on", "on")
                    try:
                        self.tg.delete(chat_id, msg.get("message_id"))
                    except Exception:
                        pass
                    self.tg.send(chat_id, "Панель сохранена: %s (сервер %s). "
                                          "Ключ в чате не храню — сообщение удалил."
                                 % (rest[0], rest[2]))
                    return True
                self.db.set("panel_on", "on" if rest[0] in ("on", "вкл") else "off")
                self.tg.send(chat_id, "Панель хостинга: %s" % self.db.flag("panel_on", "on"))
                return True
            if sub in ("shell", "шелл") and rest:
                self.db.set("shell_console", "on" if rest[0] in ("on", "вкл") else "off")
                self.tg.send(chat_id, "Шелл хоста: %s (шаблон процесса: %s)"
                             % (self.db.flag("shell_console", "off"),
                                self.db.get("shell_pattern") or "unix_args.txt"))
                return True
            lines = ["Консоль сервера из чата: /console <команда>", "",
                     "Каналы (пробуются по очереди):"]
            for name, ready_now, info in rcon.channels(self.db):
                lines.append("%s %s — %s" % ("✅" if ready_now else "❌", name, info))
            lines += ["", "Настройка RCON:",
                      "/rcon set <адрес> <порт> <пароль> — всё сразу",
                      "/rcon host <адрес> | /rcon port <порт> | /rcon pass <пароль>",
                      "/rcon test — проверить связь | /rcon on | off",
                      "/rcon wl whitelist add {nick} — своя команда вайтлиста",
                      "",
                      "Агент внутри JVM (javaagent, работает даже когда RCON закрыт):",
                      "/rcon agent <хост> <порт> <токен>",
                      "/rcon agent file <папка сервера> — через console.cmd/console.out",
                      "Панель хостинга (Pterodactyl/Pelican):",
                      "/rcon panel <url> <ключ> <uuid-сервера>",
                      "Шелл хоста (бот и сервер на одной машине):",
                      "/rcon shell on | off",
                      "",
                      "Команда вайтлиста: %s" % conf["template"]]
            self.tg.send(chat_id, "\n".join(lines))
            return True
        if cmd in ("/eva", "/ева"):
            return eva.handle(self, chat_id, arg)
        if cmd in ("/wl", "/whitelist", "/вайтлист"):
            chunks = arg.split()
            sub = chunks[0].lower() if chunks else ""
            nick = " ".join(chunks[1:]).strip()
            if sub in ("add", "+", "добавить", "доб") and nick:
                ok, out = self.wl_action(nick, add=True)
                self.tg.send(chat_id, "%s\n%s" % ("Ник %s добавлен." % nick if ok
                                                   else "Не получилось.", out[:1500]))
                return True
            if sub in ("remove", "del", "-", "убрать", "rm") and nick:
                ok, out = self.wl_action(nick, add=False)
                self.tg.send(chat_id, "%s\n%s" % ("Ник %s убран." % nick if ok
                                                   else "Не получилось.", out[:1500]))
                return True
            self.tg.send(chat_id, "Вайтлист через RCON:\n"
                                  "/wl add <ник> — добавить\n"
                                  "/wl remove <ник> — убрать\n\n"
                                  "Связь: /rcon test")
            return True
        if cmd in ("/moder", "/модерация", "/фильтр"):
            parts_low = [p.lower() for p in arg.split()]
            if parts_low and parts_low[0] in ("ai", "ии", "иии"):
                val = parts_low[1] if len(parts_low) > 1 else (
                    "off" if self.db.flag("moder_ai", "on") == "on" else "on")
                self.db.set("moder_ai", "on" if val in ("on", "вкл", "1", "да") else "off")
                self.tg.send(chat_id, "Проверка ИИ: %s" % self.db.flag("moder_ai", "on"))
                return True
            if parts_low and parts_low[0] in ("on", "off", "вкл", "выкл", "1", "0",
                                              "да", "нет"):
                val = parts_low[0]
            elif arg:
                val = "off" if arg.lower() in ("off", "выкл", "0", "нет") else "on"
            else:
                val = "off" if self.db.flag("moder", "on") == "on" else "on"
            self.db.set("moder", "on" if val in ("on", "вкл", "1", "да") else "off")
            info = moder.engine_info()
            self.tg.send(chat_id,
                         "Фильтр оскорблений: %s\n"
                         "Проверка ИИ: %s\n"
                         "Словарь: %d слов и фраз, %d языков и письменностей\n"
                         "Своих слов: %d\n\n"
                         "Обычный мат, брань без адресата, ссылки и цифры не отмечаются.\n"
                         "Проверить фразу: /check <текст>\n"
                         "Включить или выключить ИИ: /moder ai on|off"
                         % (self.db.flag("moder", "on"), self.db.flag("moder_ai", "on"),
                            info["hard"] + info["soft"] + info["sub"] + info["phrases"],
                            info["languages"], len(self.db.bad_words())))
            return True
        if cmd in ("/check", "/проверка", "/фильтр-тест", "/testmoder"):
            if not arg:
                self.tg.send(chat_id, "Напиши: /check <текст> — покажу, что найдёт фильтр.\n"
                                      "Например: /check ты тупой")
                return True
            sample = arg.strip()
            hard = moder.detect_full(sample, directed=True)
            plain = moder.detect_full(sample, directed=False)
            if hard:
                text = ("Фраза: %s\n\nНарушение: %s\nТип: %s\nЯзык: %s\nКак поймано: %s"
                        % (sample[:200], hard["word"], hard["category"],
                           moder.lang_name(hard.get("lang")) or "—",
                           hard.get("method") or "—"))
                if not plain:
                    text += ("\n\nБез обращения к человеку это не нарушение "
                             "(нужно «ты», ответ на сообщение или упоминание).")
            else:
                text = "Фраза: %s\n\nНарушений нет." % sample[:200]
            self.tg.send(chat_id, text)
            return True
        if cmd in ("/addbad", "/добавитьмат"):
            if not arg:
                self.tg.send(chat_id, "Напиши: /addbad <слово или фраза>\n"
                                      "Слово попадёт в список оскорблений.")
                return True
            added = self.db.add_bad_word(arg.strip())
            moder.reload(self.db.bad_words())
            self.tg.send(chat_id, "Добавлено в фильтр: %s" % arg.strip() if added
                         else "Такое слово уже в фильтре.")
            return True
        if cmd == "/delbad":
            if not arg:
                self.tg.send(chat_id, "Напиши: /delbad <слово>")
                return True
            self.db.del_bad_word(arg.strip())
            moder.reload(self.db.bad_words())
            self.tg.send(chat_id, "Убрано из фильтра: %s" % arg.strip())
            return True
        if cmd in ("/badlist", "/badwords"):
            words = self.db.bad_words()
            self.tg.send(chat_id, "Свои слова в фильтре (плюс встроенный список оскорблений "
                                  "на разных языках): %s" % (", ".join(words) if words else "пусто"))
            return True
        if cmd in ("/accept", "/принять"):
            if not arg.isdigit():
                self.tg.send(chat_id, "Напиши: /accept <номер заявки>")
                return True
            self.tg.send(chat_id, self.accept_ticket(int(arg), user))
            return True
        if cmd in ("/reject", "/отклонить"):
            chunks = arg.split()
            if not chunks or not chunks[0].isdigit():
                self.tg.send(chat_id, "Напиши: /reject <номер заявки> <причина>")
                return True
            tid = int(chunks[0])
            reason = " ".join(chunks[1:]).strip()
            self.tg.send(chat_id, self.reject_ticket(tid, reason, user))
            return True
        if cmd == "/say":
            if not arg:
                return True
            sent, failed = 0, 0
            for target in self.db.all_user_ids():
                try:
                    self.tg.send(target, arg)
                    sent += 1
                except Exception:
                    failed += 1
                time.sleep(0.05)
            self.tg.send(chat_id, "Рассылка: доставлено %d, не доставлено %d." % (sent, failed))
            return True
        return False

    # ------------------------------------------------------------------
    # заявки: команды владельца и помощника
    # ------------------------------------------------------------------
    def help_command(self, msg, text, chat_id, user, cmd, arg):
        chunks = arg.split()
        tid = None
        if chunks and chunks[0].lstrip("#").isdigit():
            tid = int(chunks[0].lstrip("#"))
        rest = " ".join(chunks[1:]).strip()

        if cmd in ("/tickets", "/zayavki") and not tid:
            self.tg.send(chat_id, self.help_queue_text(10))
            return True
        if cmd == "/helpq":
            self.tg.send(chat_id, self.help_queue_text(15))
            return True
        if tid is None:
            self.tg.send(chat_id, "Напиши номер заявки, например: %s 12" % cmd)
            return True

        ticket = self.db.ticket(tid)
        if not ticket:
            self.tg.send(chat_id, "Заявка #%d не найдена." % tid)
            return True
        if cmd == "/answer":
            if not rest:
                self.tg.send(chat_id, "Напиши: /answer %d текст ответа" % tid)
                return True
            self.reply_to_user(ticket, rest)
            self.tg.send(chat_id, "Ответ по заявке #%d отправлен." % tid)
            return True
        if cmd == "/card":
            self.send_ticket_card(chat_id, ticket)
            return True
        if cmd == "/take":
            self.tg.send(chat_id, self.take_ticket(tid, user))
            return True
        if cmd == "/close":
            self.tg.send(chat_id, self.close_ticket_admin(tid, user))
            return True
        self.tg.send(chat_id, self.help_queue_text(10))
        return True

    def send_ticket_card(self, chat_id, ticket):
        """Показать карточку заявки в чате помощника."""
        body = (ticket.get("body") or "").strip()
        text = ("Заявка #%d\nТип: %s%s\nСтатус: %s\nОт: %s (id %s)\n\n%s\n\n"
                "Ответ: реплай на это сообщение или /answer %d <текст>. "
                "Закрыть: /close %d" % (
                    ticket["id"], ticket.get("kind") or "",
                    (" · " + ticket["subject"]) if ticket.get("subject") else "",
                    self.status_name(ticket.get("status")),
                    ticket.get("username") or "-", ticket.get("user_id"),
                    body, ticket["id"], ticket["id"]))
        sent = self.tg.send(chat_id, text)
        if sent:
            self.db.link(chat_id, sent.get("message_id"), "ticket", ticket["id"])

    # ------------------------------------------------------------------
    # админ: ответы на заявки
    # ------------------------------------------------------------------
    def admin_message(self, msg, text, chat_id, user):
        uid = user.get("id")
        reply_to = (msg.get("reply_to_message") or {})
        is_admin = self._is_helper(uid, user.get("username"))
        if not is_admin:
            return False
        if reply_to.get("message_id"):
            link = self.db.lookup(chat_id, reply_to["message_id"])
            if link and str(link["role"]).startswith("reject:"):
                tid = int(str(link["role"]).split(":", 1)[1])
                reason = "" if text.strip().lower() in ("нет", "-", "без причины") else text.strip()
                self.tg.send(chat_id, self.reject_ticket(tid, reason, user))
                return True
            if link and link["role"] == "ticket":
                ticket = self.db.ticket(link["ref"])
                if ticket:
                    self.reply_to_user(ticket, text)
                    self.tg.send(chat_id, "Ответ отправлен игроку по заявке #%d." % ticket["id"])
                    return True
        return False

    def reply_to_user(self, ticket, text):
        uid = ticket["user_id"]
        ok = True
        try:
            self.tg.send(uid, "Ответ по обращению #%d:\n%s" % (ticket["id"], text),
                         keyboard=keyboard([[("Мои заявки", "m:mytickets")],
                                            [("В меню", "m:menu")]]))
        except Exception as exc:
            ok = False
            log.warning("не смог ответить игроку %s: %s", uid, exc)
            self.tg.send(config.SUPER_ADMIN, "Не смог доставить ответ игроку %s (заявка #%d)."
                         % (uid, ticket["id"]))
        self.db.close_ticket(ticket["id"], text)
        if ok and (ticket.get("kind") or "").startswith("Заявка на помощь"):
            ticket["status"] = "answered"
            self._refresh_admin_msg(ticket, "[ОТВЕТ ОТПРАВЛЕН]")

    # ------------------------------------------------------------------
    # вайтлист и решения по анкетам
    # ------------------------------------------------------------------
    def _ticket_nick(self, ticket):
        body = (ticket or {}).get("body") or ""
        for line in body.splitlines():
            low = line.strip().lower()
            if low.startswith("ник в игре") or low.startswith("ник:"):
                return line.split(":", 1)[1].strip() if ":" in line else ""
        return ""

    def wl_action(self, nick, add=True):
        conf = rcon.cfg(self.db)
        tpl = conf["template"] if add else conf["untemplate"]
        try:
            cmd = tpl.format(nick=nick)
        except Exception:
            cmd = "%s %s" % (tpl, nick)
        ok, out = rcon.run(self.db, cmd)
        if not ok and not out:
            out = "RCON не отвечает. Проверь: /rcon test"
        return ok, out

    def _admin_card(self, ticket, mark="", extra=""):
        body = (ticket.get("body") or "").strip()
        return ("Заявка #%d\nТип: %s\nОт: %s (id %s)\n\n%s\n\n%s%s"
                % (ticket["id"], ticket.get("kind") or "", ticket.get("username") or "",
                   ticket.get("user_id") or "", body, mark, extra))

    def _refresh_admin_msg(self, ticket, mark="", extra="", buttons=None):
        """Обновить карточки заявки у всех, кому она ушла."""
        cards = self.db.ticket_msgs(ticket.get("id") or 0)
        if not cards and ticket.get("admin_msg"):
            cards = [[config.SUPER_ADMIN, ticket["admin_msg"]]]
        if not cards:
            return
        text = self._admin_card(ticket, mark, extra)
        for chat_id, mid in cards:
            try:
                self.tg.edit(chat_id, mid, text,
                             keyboard=buttons if buttons is not None else keyboard([]))
            except Exception as exc:
                log.info("не смог обновить карточку заявки #%s в %s: %s",
                         ticket.get("id"), chat_id, exc)

    def accept_ticket(self, tid, admin_user=None):
        ticket = self.db.ticket(tid)
        if not ticket:
            return "Заявка #%d не найдена." % tid
        if (ticket.get("kind") or "") != "Анкета":
            return ("#%d — это не анкета, а «%s». Отвечай /answer %d <текст>, "
                    "в работу — /take %d, закрыть — /close %d."
                    % (tid, ticket.get("kind") or "заявка", tid, tid, tid))
        if ticket.get("status") in ("accepted", "rejected", "answered", "closed"):
            return "По заявке #%d решение уже принято." % tid
        nick = self._ticket_nick(ticket)
        lines = ["Заявка #%d принята." % tid]
        added = False
        if nick:
            ok, out = self.wl_action(nick, add=True)
            added = ok
            if ok:
                lines.append("Вайтлист: %s добавлен. Ответ сервера: %s" % (nick, out[:300]))
            else:
                lines.append("Вайтлист: не сработало — %s" % out[:300])
                lines.append("Добавь вручную: /console %s"
                             % rcon.cfg(self.db)["template"].format(nick=nick))
        else:
            lines.append("Ник в анкете не найден — добавь вручную: /console whitelist add <ник>")
        self.db.set_ticket_status(tid, "accepted", "анкета принята")
        text = "Заявка #%d одобрена!" % tid
        if nick and added:
            text += "\nНик %s добавлен в белый список — заходи на сервер." % nick
        elif nick:
            text += "\nНик %s добавят в белый список в ближайшее время." % nick
        text += "\n\n%s" % sinfo.get(self.db, "whitelist")
        try:
            self.tg.send(ticket["user_id"], text)
            lines.append("Игроку отправлено уведомление.")
        except Exception as exc:
            lines.append("Игроку уведомление не ушло: %s" % exc)
        self._refresh_admin_msg(ticket, "[ПРИНЯТА]")
        return "\n".join(lines)

    def reject_ticket(self, tid, reason="", admin_user=None):
        ticket = self.db.ticket(tid)
        if not ticket:
            return "Заявка #%d не найдена." % tid
        if (ticket.get("kind") or "") != "Анкета":
            return ("#%d — это «%s», а не анкета. Заявку на помощь закрывают "
                    "командой /close %d." % (tid, ticket.get("kind") or "заявка", tid))
        if ticket.get("status") in ("accepted", "rejected", "answered", "closed"):
            return "По заявке #%d решение уже принято." % tid
        reason = (reason or "").strip()
        self.db.set_ticket_status(tid, "rejected", reason or "отклонена")
        text = "Заявка #%d отклонена." % tid
        if reason:
            text += "\nПричина: %s" % reason
        text += "\n\nМожешь подать анкету заново позже или задать вопросы администрации."
        sent = "Игроку отправлено уведомление."
        try:
            self.tg.send(ticket["user_id"], text)
        except Exception as exc:
            sent = "Игроку уведомление не ушло: %s" % exc
        self._refresh_admin_msg(ticket, "[ОТКЛОНЕНА]", ("\nПричина: %s" % reason) if reason else "")
        return "Заявка #%d отклонена. %s" % (tid, sent)

    # ---------- заявки на помощь: взять / закрыть / статус ----------
    @staticmethod
    def status_name(status):
        return TICKET_STATUS_RU.get((status or "new").lower(), status or "новая")

    def take_ticket(self, tid, admin_user=None):
        ticket = self.db.ticket(tid)
        if not ticket:
            return "Заявка #%d не найдена." % tid
        if ticket.get("status") in ("answered", "closed", "rejected", "accepted"):
            return "Заявка #%d уже закрыта (%s)." % (tid, self.status_name(ticket.get("status")))
        self.db.set_ticket_status(tid, "taken", ticket.get("answer") or "")
        ticket["status"] = "taken"
        who = (admin_user or {}).get("first_name") or "администрация"
        self._refresh_admin_msg(ticket, "[В РАБОТЕ]",
                                "\nВзял в работу: %s" % who,
                                buttons=self._ticket_buttons(tid))
        try:
            self.tg.send(ticket["user_id"],
                         "Заявка #%d взята в работу. Ответ придёт в этот чат." % tid,
                         keyboard=keyboard([[("Мои заявки", "m:mytickets")],
                                            [("В меню", "m:menu")]]))
        except Exception as exc:
            log.info("не смог уведомить игрока по заявке #%s: %s", tid, exc)
        return "Заявка #%d в работе." % tid

    def close_ticket_admin(self, tid, admin_user=None):
        ticket = self.db.ticket(tid)
        if not ticket:
            return "Заявка #%d не найдена." % tid
        if ticket.get("status") in ("closed", "rejected"):
            return "Заявка #%d уже закрыта." % tid
        self.db.set_ticket_status(tid, "closed", ticket.get("answer") or "")
        ticket["status"] = "closed"
        self._refresh_admin_msg(ticket, "[ЗАКРЫТА]")
        try:
            self.tg.send(ticket["user_id"],
                         "Заявка #%d закрыта.\nЕсли проблема повторится или нужна помощь снова — "
                         "оформи новую: /zayavka" % tid,
                         keyboard=keyboard([[("В меню", "m:menu")]]))
        except Exception as exc:
            log.info("не смог уведомить игрока о закрытии #%s: %s", tid, exc)
        return "Заявка #%d закрыта." % tid

    def my_tickets_text(self, uid):
        rows = self.db.user_tickets(uid, 5)
        if not rows:
            return "У тебя пока нет заявок.\nНужна помощь — кнопка «Нужна помощь» или /zayavka."
        lines = ["Твои заявки:"]
        for row in rows:
            created = time.strftime("%d.%m %H:%M", time.localtime(row.get("created_at") or 0))
            subject = (" · " + row["subject"]) if row.get("subject") else ""
            ans = (row.get("answer") or "").strip()
            lines.append("#%d — %s%s — %s (%s)" % (
                row["id"], row.get("kind") or "Заявка", subject,
                self.status_name(row.get("status")), created))
            if ans and len(ans) <= 200 and ans.lower() not in ("анкета принята", "отклонена"):
                lines.append("    ответ: %s" % ans)
        lines.append("\nОтвет по заявке приходит сюда в личку. Новая заявка: /zayavka")
        return "\n".join(lines)

    def help_queue_text(self, limit=10):
        rows = [r for r in self.db.open_tickets(40)
                if (r.get("kind") or "").startswith("Заявка на помощь")]
        if not rows:
            return "Открытых заявок на помощь нет."
        lines = ["Открытые заявки на помощь:"]
        for row in rows[:limit]:
            created = time.strftime("%d.%m %H:%M", time.localtime(row.get("created_at") or 0))
            lines.append("#%d · %s · %s · %s" % (
                row["id"], row.get("subject") or "Другое",
                self.status_name(row.get("status")), created))
        lines.append("\nОткрыть карточку: /card <номер>. Ответить: /answer <номер> <текст>. "
                     "Закрыть: /close <номер>")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # формы
    # ------------------------------------------------------------------
    def start_form(self, chat_id, uid, kind):
        form = FORMS[kind]
        self.state[uid] = {"form": kind, "step": 0, "data": {}}
        self.tg.send(chat_id, "%s\n%s\n\n1/%d. %s" % (
            form["title"], form["hint"], len(form["steps"]), form["steps"][0][0]),
            keyboard=keyboard([[("Отмена", "m:cancel")]]))

    # ---------- заявка на помощь ----------
    def start_help(self, chat_id, uid):
        """Заявка на помощь. Если открытая уже есть — предлагаем её дополнить."""
        open_ticket = self.db.user_open_ticket(uid, "Заявка на помощь")
        if open_ticket:
            age = time.time() - (open_ticket.get("created_at") or 0)
            if age > config.HELP_DUP_HOURS * 3600:
                open_ticket = None   # старая заявка висит слишком долго — не блокируем новую
        if open_ticket:
            self.state[uid] = {"form": "help_add", "ticket": open_ticket["id"]}
            self.tg.send(chat_id,
                         "У тебя уже есть открытая заявка #%d — она ещё в работе, "
                         "ответ придёт в этот чат.\nЕсли нужно что-то добавить — напиши здесь, "
                         "я приложу к заявке." % open_ticket["id"],
                         keyboard=keyboard([[("Мои заявки", "m:mytickets")],
                                            [("В меню", "m:menu")]]))
            return
        self.state[uid] = {"form": "help_cat", "step": 0, "data": {}}
        self.tg.send(chat_id,
                     "Заявка на помощь\n"
                     "Выбери, что ближе к твоему случаю, — или напиши своими словами, "
                     "я подберу категорию и задам несколько вопросов.",
                     keyboard=help_cat_keyboard())

    def start_help_form(self, chat_id, uid, cat):
        cat = cat if cat in HELP_CAT_TITLE else "other"
        form = FORMS["help"]
        self.state[uid] = {"form": "help", "step": 0, "data": {"cat": cat}}
        self.tg.send(chat_id, "%s\nКатегория: %s\n%s\n\n1/%d. %s" % (
            form["title"], HELP_CAT_TITLE[cat], form["hint"],
            len(form["steps"]), form["steps"][0][0]),
            keyboard=keyboard([[("Отмена", "m:cancel")]]))

    def _form_file(self, msg):
        """Вложение из сообщения игрока (скриншот/файл), если есть."""
        msg = msg or {}
        for key, kind in (("photo", "фото"), ("document", "файл"),
                          ("video", "видео"), ("animation", "гиф")):
            if msg.get(key):
                return {"kind": kind, "chat_id": (msg.get("chat") or {}).get("id"),
                        "message_id": msg.get("message_id")}
        return None

    def _copy_file(self, chat_id, file_info):
        try:
            self.tg.call("copyMessage", chat_id=chat_id,
                         from_chat_id=file_info["chat_id"],
                         message_id=file_info["message_id"])
            return True
        except Exception as exc:
            log.info("не смог переслать вложение к заявке: %s", exc)
            return False

    def form_step(self, msg, state, text, chat_id, user):
        kind = state.get("form")
        uid = user.get("id")
        text = (text or "").strip()

        # выбор категории заявки на помощь
        if kind == "help_cat":
            self.start_help_form(chat_id, uid, help_category(text))
            return

        # дополнение к уже открытой заявке
        if kind == "help_add":
            tid = state.get("ticket")
            if not text:
                self.tg.send(chat_id, "Напиши текстом, что добавить к заявке. "
                                      "Или /menu, чтобы выйти.", keyboard=BACK_MENU)
                return
            self.state.pop(uid, None)
            ticket = self.db.ticket(tid) if tid else None
            if not ticket:
                self.tg.send(chat_id, "Заявка уже недоступна. Оформи новую: /zayavka",
                             keyboard=MAIN_MENU)
                return
            self.db.append_ticket(tid, text)
            self.notify_ticket_note(ticket, text, user)
            self.tg.send(chat_id, "Добавил к заявке #%d. Ответ придёт сюда." % tid,
                         keyboard=keyboard([[("Мои заявки", "m:mytickets")],
                                            [("В меню", "m:menu")]]))
            return

        form = FORMS.get(kind)
        if not form:
            self.state.pop(uid, None)
            return
        steps = form["steps"]
        step = state.get("step", 0)
        if step >= len(steps):
            step = 0
        label, key = steps[step]

        file_info = self._form_file(msg)
        if file_info and key == "proof":
            state["data"][key] = "вложение (%s)" % file_info["kind"]
            state["file"] = file_info
        elif not text:
            self.tg.send(chat_id, "Нужен текст: %s\nЕсли нечего написать — поставь «нет»." % label,
                         keyboard=keyboard([[("Отмена", "m:cancel")]]))
            return
        else:
            state["data"][key] = text

        state["step"] = step + 1
        if state["step"] < len(steps):
            label, key = steps[state["step"]]
            self.tg.send(chat_id, "%d/%d. %s" % (state["step"] + 1, len(steps), label),
                         keyboard=keyboard([[("Отмена", "m:cancel")]]))
            return

        data = state["data"]
        file_info = state.get("file")
        self.state.pop(uid, None)
        body = "\n".join("%s: %s" % (steps[i][0], data.get(steps[i][1], "-"))
                         for i in range(len(steps)))

        if kind == "help":
            cat = data.get("cat") or "other"
            subject = HELP_CAT_TITLE.get(cat, "Другое")
            full = "Категория: %s\n%s" % (subject, body)
            tid = self.db.new_ticket(user, "Заявка на помощь", full, subject=subject)
            self.to_admin(tid, user, "Заявка на помощь", full, file_info=file_info)
            self.tg.send(chat_id,
                         "Заявка #%d принята и отправлена администрации "
                         "(категория: %s).\nОтвет придёт в этот чат, статус — /moi." % (tid, subject),
                         keyboard=keyboard([[("Мои заявки", "m:mytickets")],
                                            [("В меню", "m:menu")]]))
            return

        tid = self.db.new_ticket(user, form["title"], body)
        self.to_admin(tid, user, form["title"], body, file_info=file_info)
        self.tg.send(chat_id, "Заявка #%d принята и отправлена администрации. Ответ придёт в этот чат."
                     % tid, keyboard=MAIN_MENU)

    def notify_ticket_note(self, ticket, text, user):
        """Сообщить админам, что игрок дополнил заявку."""
        header = ("Заявка #%d — дополнение от игрока\nОт: %s%s (id %s)\n\n%s\n\n"
                  "Ответить можно реплаем на это сообщение или /answer %d <текст>."
                  % (ticket["id"], user.get("first_name") or "",
                     (" @" + user["username"]) if user.get("username") else "",
                     user.get("id"), text.strip(), ticket["id"]))
        for target in self.db.ticket_msgs(ticket["id"]) or []:
            chat_id, mid = target
            try:
                self.tg.send(chat_id, header, reply_to=mid)
            except Exception as exc:
                log.info("не смог отправить дополнение по заявке: %s", exc)

    # ---------- карточки заявок ----------
    def _ticket_buttons(self, tid, with_take=False):
        rows = []
        take = [("Взять в работу", "h:take:%d" % tid)] if with_take else []
        if take:
            rows.append(take + [("Ответить", "h:ans:%d" % tid)])
        else:
            rows.append([("Ответить", "h:ans:%d" % tid)])
        rows.append([("Закрыть", "h:close:%d" % tid), ("Профиль игрока", "h:who:%d" % tid)])
        return keyboard(rows)

    def to_admin(self, tid, user, kind, body, file_info=None):
        who = user.get("username")
        is_help = kind.startswith("Заявка на помощь")
        hint = "Чтобы ответить — ответь реплаем на это сообщение или напиши /answer %d <текст>." % tid
        if kind == "Анкета":
            hint = ("Ник из анкеты добавляется в вайтлист одной кнопкой. "
                    "Отклонить можно с причиной: /reject %d <причина>" % tid)
        elif is_help:
            hint = ("Ответить — реплаем на это сообщение или /answer %d <текст>. "
                    "Взять в работу и закрыть — кнопками ниже." % tid)
        header = ("Заявка #%d\nТип: %s\nОт: %s%s (id %s)\n\n%s\n\n%s") % (
            tid, kind, user.get("first_name") or "", (" @" + who) if who else "",
            user.get("id"), body, hint)
        if is_help:
            buttons = self._ticket_buttons(tid, with_take=True)
            targets = []
            for target in config.HELP_ADMINS:
                if target not in targets:
                    targets.append(target)
        else:
            buttons = None
            targets = [config.SUPER_ADMIN]
            if kind == "Анкета":
                buttons = keyboard([[("Принять в вайтлист", "w:ok:%d" % tid),
                                     ("Отклонить", "w:no:%d" % tid)]])

        cards = []
        for target in targets:
            sent = None
            try:
                sent = self.tg.send(target, header, keyboard=buttons)
            except Exception as exc:
                log.warning("не смог отправить заявку #%s в %s: %s", tid, target, exc)
            if not sent:
                continue
            cards.append([target, sent.get("message_id")])
            self.db.link(target, sent.get("message_id"), "ticket", tid)
            if file_info:
                self._copy_file(target, file_info)
        if cards:
            self.db.set_ticket_msgs(tid, cards)
            self.db.set_admin_msg(tid, cards[0][1])

    # ------------------------------------------------------------------
    # ответы
    # ------------------------------------------------------------------
    def _cache_key(self, question, emotions, emoji):
        text = " ".join((question or "").lower().replace("ё", "е").split())
        text = re.sub(r"[^0-9a-zа-я\s]+", "", text)[:160]
        return "%s|%d%d" % (text, int(bool(emotions)), int(bool(emoji)))

    def _cache_get(self, key):
        with self.answers_lock:
            item = self.answers.get(key)
        if not item:
            return ""
        if time.time() - item[0] > config.AI_CACHE_TTL:
            with self.answers_lock:
                self.answers.pop(key, None)
            return ""
        return item[1]

    def _cache_put(self, key, text):
        if not text or len(text) < 20:
            return
        with self.answers_lock:
            if len(self.answers) > config.AI_CACHE_SIZE:
                self.answers.clear()
            self.answers[key] = (time.time(), text)

    def _dm_allowed(self, uid):
        """Лимит платных ответов в личке: в минуту и в сутки."""
        now = time.time()
        slot = self.dm_budget.get(uid)
        if not slot:
            slot = [now, 0, now, 0]
            self.dm_budget[uid] = slot
        if now - slot[0] >= 60:
            slot[0], slot[1] = now, 0
        if now - slot[2] >= 86400:
            slot[2], slot[3] = now, 0
        if slot[1] >= config.DM_AI_PER_MIN:
            return "min"
        if slot[3] >= config.DM_AI_PER_DAY:
            return "day"
        slot[1] += 1
        slot[3] += 1
        if len(self.dm_budget) > 3000:
            for key in [k for k, v in list(self.dm_budget.items()) if now - v[2] > 86400][:500]:
                self.dm_budget.pop(key, None)
        return ""

    def answer(self, question, chat_id, user, group=False, reply_to=None, keyboard=None):
        question = (question or "").strip()
        if not question:
            return "Напиши вопрос.", False

        direct = self._direct_answer(question)
        if direct:
            return direct, False

        emotions, emoji = self._style()
        key = self._cache_key(question, emotions, emoji)
        cached = self._cache_get(key)
        if cached:
            return cached, False

        uid = (user or {}).get("id")
        if not group and uid:
            reason = self._dm_allowed(uid)
            if reason == "min":
                return ("Слишком часто. Подожди минуту и спроси снова.", False)
            if reason == "day":
                return ("На сегодня лимит вопросов исчерпан. Продолжим завтра, "
                        "а если вопрос срочный — напиши администрации.", False)

        server_text = self.server_text()
        delivered = False
        reply = ""
        with Typing(self.tg, chat_id):
            context = self._gather(question)
            reply, delivered = self._generate(question, chat_id, server_text, context,
                                              stream_to=chat_id, reply_to=reply_to,
                                              keyboard=keyboard, emotions=emotions,
                                              emoji=emoji, group=group)
            if reply:
                self._cache_put(key, reply)
        if not reply:
            reply = self.local_fallback(question)
        return reply, delivered

    def _style(self):
        return (self.db.flag("emotions", "on") == "on", self.db.flag("emoji", "off") == "on")

    def _direct_answer(self, question):
        """Прямые факты из карточки сервера — без модели, чтобы не было выдумок."""
        low = question.lower()
        v = sinfo.values(self.db)
        name = v["about"]

        if re.search(r"(какой\s+ip|какой\s+айпи|\bip\b|айпи|адрес\s+сервер|как\s+зайти|"
                     r"как\s+подключит|какой\s+порт|порт\s+сервер|верси[яю]\s+сервер|"
                     r"сервер\s+верси|как\s+играть|начать\s+играть|куда\s+зайти)", low):
            return ("Сервер %s\nJava: %s\nBedrock: %s\nВерсия Java: %s\nВерсия Bedrock: %s\n\n%s"
                    % (name, v["java"], v["bedrock"], v["javaver"], v["bedrockver"], v["whitelist"]))
        if re.search(r"(вайтлист|вайт-лист|бел(ый|ом)\s+список|как\s+попасть|добав(ь|ят|ить)\s+меня)", low):
            return "Вход на %s:\n%s\n\nЧат сервера: %s" % (name, v["whitelist"], v["chat"])
        if re.search(r"(верси[яю]|версии|version)", low) and re.search(
                r"(сервер|java|джава|bedrock|бедрок|майн|minecraft|играть|нужн|поддерж)", low):
            return ("Версии сервера %s\nJava: %s\nBedrock: %s\nJava-адрес: %s\nBedrock-адрес: %s"
                    % (name, v["javaver"], v["bedrockver"], v["java"], v["bedrock"]))
        if re.search(r"(ресурс\s*пак|ресурспак|ресурс-пак|рп\b|текстур\s*пак|текстурпак|модпак)", low):
            return "Ресурс-пак (только Java): %s" % v["respack"]
        if re.search(r"(дискорд|discord)", low):
            return "Discord сервера %s: %s" % (name, v["discord"])
        if re.search(r"(правил|бан\b|запрещ)", low) and not re.search(
                r"\b(как|почему|зачем|за\s+что|можно\s+ли)\b", low):
            return "Правила %s: %s" % (name, v["rules"])
        if re.search(r"(донат|донейт|поддержк|поддержать\s+сервер|финансов|пожертвован|"
                     r"звёзд|звезд|звёздочк|донатик|привилег)", low):
            return ("Донат-привилегии: %s\nФинансовая поддержка: %s"
                    % (v["donate"], v["support"]))
        if re.search(r"(вся\s+информац|информация\s+о\s+сервер|ссылка\s+на\s+сервер|"
                     r"путеводител|закреп)", low):
            return "Вся информация о сервере %s: %s\nЧат: %s" % (name, v["info"], v["chat"])
        if re.search(r"(чат\s+сервер|чат\s+группы|группа\s+сервер|тг\s+чат|телеграм\s+чат|ссылк\S*\s+на\s+чат)", low):
            return "Чат сервера: %s\nДискорд: %s\nКанал владельца: %s" % (v["chat"], v["discord"], v["channel"])
        if re.search(r"(канал\s+владельц|канал\s+сервер|основной\s+канал)", low):
            return "Канал владельца: %s" % v["channel"]
        if re.search(r"(сколько\s+игрок|кто\s+онлайн|онлайн|статус\s+сервер|сервер\s+работает|"
                     r"сервер\s+лежит|сервер\s+упал|лаги|пинг|tps)", low):
            return self.server_text(force=True)
        if re.search(r"(какие\s+плагины|список\s+плагин|плагины\s+сервер|"
                     r"какие\s+моды|что\s+за\s+плагин|плагин\s+отвечает)", low):
            return self.plugins_text()
        if re.search(r"(какие\s+датапак|список\s+датапак|датапак\s+сервер|"
                     r"какие\s+датапаки|что\s+добавляют\s+датапак)", low):
            return self.datapacks_text()
        if re.search(r"(какие\s+команды|что\s+умеет|что\s+ты\s+умеешь)", low) and \
                not re.search(r"(сервер|плагин|датапак|разлом|свадьб|скорб)", low):
            return HELP_TEXT
        known = mckb.lookup(question)
        if known and re.search(r"(майн|minecraft|моб|руда|алмаз|незерит|зачаров|эндер|"
                               r"незер|портал|ферм|редстоун|зель|крафт|кирк|броня|"
                               r"варден|пещер|шахтер|спавн|жител|деревн|элитр|дракон)", low):
            return known
        return None

    def _gather(self, question):
        """Собирает весь контекст: знания группы, факты, крафты, интернет, видео."""
        words = ai.keywords(question)
        facts = self.db.facts(20)
        facts_text = "\n".join("- " + f["text"] for f in facts[:8])
        # Знания из группы нужны только по вопросам про сервер: на вопросы
        # про механики Minecraft они не влияют, а токенов едят много.
        server_related = bool(SERVERQ_RE.search(question) or re.search(
            r"(сервер|вайтлист|правил|донат|адрес|ник|подключ|зайти|чат|канал)",
            question, re.IGNORECASE))
        knowledge = self.db.search_knowledge(words, limit=5) if server_related else []
        knowledge_text = "\n".join(
            "- %s: %s" % (k.get("username") or "игрок", k["text"][:160]) for k in knowledge)

        craft = ""
        if CRAFT_RE.search(question) or crafts.find(question, 1):
            craft = ai.craft_context(question)

        web_block = ""
        videos = []
        if VIDEO_RE.search(question):
            videos = web.youtube(question, limit=2)
        own_server = bool(SERVERQ_RE.search(question))
        # Поиск в интернете — только когда игрок прямо просит искать.
        # Раньше он подключался на любой незнакомый вопрос и забивал промпт.
        if not own_server and SEARCH_RE.search(question):
            web_block = web.search_block(question, limit=3)

        # Знания о плагинах, датапаках и кастомных плагинах сервера.
        server_kb = kb.block(question, limit_chars=3200)
        if not server_kb:
            low = question.lower()
            if re.search(r"(плагин|plugin)", low):
                server_kb = self.plugins_text(compact=True)
            elif re.search(r"(датапак|datapack)", low):
                server_kb = self.datapacks_text(compact=True)

        return {"facts": facts_text, "knowledge": knowledge_text, "craft": craft,
                "web": web_block, "videos": videos, "words": words,
                "mckb": mckb.block(question), "server_kb": server_kb,
                "info": sinfo.block_compact(self.db)}

    def _generate(self, question, chat_id, server_text, context, stream_to=None,
                  reply_to=None, keyboard=None, emotions=True, emoji=False, group=False):
        videos = context.get("videos") or []
        extra_parts = []
        known = context.get("mckb") or ""
        if known:
            extra_parts.append("СПРАВКА ПО МЕХАНИКАМ MINECRAFT (используй как факты, не выдумывай):\n"
                               + known)
        if videos:
            extra_parts.append("Ссылки на видео, которые надо выдать игроку (приведи их как есть):\n"
                               + "\n".join("%s — %s" % (v["title"], v["url"]) for v in videos))
        system = ai.build_system(
            server_text=context.get("info", ""),
            knowledge=context.get("knowledge", ""),
            facts=context.get("facts", ""),
            craft=context.get("craft", ""),
            web_block=context.get("web", ""),
            server_kb=context.get("server_kb", ""),
            extra="\n\n".join(extra_parts),
            emotions=emotions,
            emoji=emoji,
            server_name=sinfo.get(self.db, "about"),
            volatile=server_text,
        )
        messages = [{"role": "system", "content": system}]
        if chat_id:
            for item in self.db.history(chat_id)[-config.HISTORY_TURNS:]:
                content = (item.get("content") or "").strip()
                if not content:
                    continue
                if len(content) > config.HISTORY_CHARS:
                    content = content[:config.HISTORY_CHARS].rstrip() + "…"
                messages.append({"role": item.get("role") or "user", "content": content})
        messages.append({"role": "user", "content": question})

        writer = None
        if stream_to:
            writer = Typewriter(self.tg, stream_to, reply_to=reply_to,
                                keyboard=keyboard, group=group)
        text = ai.chat(messages, on_delta=(writer.feed if writer else None), strip=not emoji)
        if videos and text and "youtube.com" not in text and "youtu.be" not in text:
            text += "\n\nВидео по теме:\n" + self._video_lines(videos)
        if writer:
            message_id = writer.finish(text)
            return text, bool(message_id)
        return text, False

    def craft_answer(self, query):
        recs = crafts.find(query, limit=3)
        if recs:
            return "\n---\n".join(r.render() for r in recs)
        made = crafts.what_from(query, limit=8)
        if made:
            return "Из этого можно сделать:\n" + "\n".join("- " + r.out for r in made)
        return ("В моей базе нет рецепта «%s». Уточни название предмета "
                "(например: поршень, кирка, торт)." % query)

    # ------------------------------------------------------------------
    # знания о плагинах и датапаках
    # ------------------------------------------------------------------
    def _catalog_text(self, source, title, hint):
        items = kb.catalog(source)
        if not items:
            return ("Знаний о %s пока нет. Администратор может проверить /kb "
                    "и папку knowledge." % title.lower())
        lines = ["%s (%d):" % (title, len(items))]
        for name, brief in items:
            lines.append("• %s — %s" % (name, brief) if brief else "• " + name)
        lines.append("")
        lines.append(hint)
        return "\n".join(lines)

    def plugins_text(self, compact=False):
        return self._catalog_text(
            "plugins", "Плагины сервера Aetherlyn — для игроков",
            "Подробно про любой: /plugins <название>, например /plugins разлом.\n"
            "В списке только то, чем пользуются игроки: служебные и админские плагины скрыты.\n"
            "Датапаки: /datapacks.")

    def datapacks_text(self, compact=False):
        return self._catalog_text(
            "datapacks", "Датапаки сервера Aetherlyn",
            "Подробно про любой: /datapacks <название>.\n"
            "Показаны датапаки, которые работают на сервере; "
            "несовместимые и служебные убраны из списка.\n"
            "Плагины: /plugins.")

    def knowledge_answer(self, query, source):
        """Подробный ответ о плагине/датапаке по названию."""
        entry = kb.get(query)
        if entry and (source is None or entry.source == source):
            return ai.strip_symbols(entry.render(limit=3600))
        low = (query or "").lower()
        for item in kb.entries(source):
            if low and (low in item.title.lower() or item.title.lower() in low):
                return ai.strip_symbols(item.render(limit=3600))
        found = kb.lookup(query, limit=1, min_score=2)
        if found:
            return ai.strip_symbols(found[0].render(limit=3600))
        return ("В знаниях нет «%s». Список: /plugins, /datapacks. "
                "Можно спросить обычной фразой — отвечу по базе знаний." % query)

    # ------------------------------------------------------------------
    # плавная доставка ответа
    # ------------------------------------------------------------------
    def deliver(self, chat_id, text, keyboard=None, reply_to=None, group=False):
        """Отправить ответ с плавной печатью (или сразу, если печать выключена)."""
        try:
            return smooth_send(self.tg, chat_id, text, keyboard=keyboard,
                               reply_to=reply_to, group=group)
        except Exception as exc:  # noqa: BLE001
            log.warning("плавная доставка не удалась, шлю обычно: %s", exc)
            return self.tg.send(chat_id, text, keyboard=keyboard, reply_to=reply_to)

    def local_fallback(self, question):
        """Ответ из своих баз, если модель недоступна. Бот не должен молчать."""
        entry = kb.get(question)
        if entry and entry.body:
            return entry.render(limit=2500)
        found = kb.lookup(question, limit=1)
        if found:
            return found[0].render(limit=2500)
        known = mckb.lookup(question)
        if known:
            return known
        recs = crafts.find(question, limit=1)
        if recs:
            return recs[0].render()
        return ("Сейчас не могу получить ответ от модели. Спроси ещё раз через минуту — "
                "или посмотри /plugins, /datapacks, /craft. "
                "Если вопрос про сервер, ответ есть в /info.")

    def video_answer(self, query):
        videos = web.youtube(query, limit=3)
        if not videos:
            link = ("https://www.youtube.com/results?search_query=" +
                    web.urllib.parse.quote(web.to_english(query)))
            return "Прямые результаты не пришли. Поиск по теме: %s" % link
        return "Видео по запросу «%s»:\n%s" % (query, self._video_lines(videos))

    def _video_lines(self, videos):
        return "\n".join("%s\n%s" % (v["title"], v["url"]) for v in videos)

    def web_answer(self, query):
        items = web.search(query, 6)
        if not items:
            return "Ничего не нашёл по запросу «%s»." % query
        lines = ["Результаты по запросу «%s»:" % query]
        for i, item in enumerate(items, 1):
            lines.append("%d. %s\n%s" % (i, item["title"], item["url"]))
            if item.get("snippet"):
                lines.append("   " + item["snippet"][:220])
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # песни: поиск и отправка файла
    # ------------------------------------------------------------------
    def music_handle(self, query, chat_id, user, reply_to=None, keyboard=None, sure=True):
        """Ищет песню и присылает её файлом. Работает в фоне, не тормозит бота."""
        query = (query or "").strip()
        if not query:
            self.tg.send(chat_id, "Напиши название песни или исполнителя.", reply_to=reply_to)
            return False
        note = self.tg.send(chat_id, "Ищу «%s»…" % query, reply_to=reply_to)

        def work():
            # Скачивание и конвертация — самая тяжёлая часть по памяти.
            # Пускаем не больше двух одновременно, чтобы бот не падал от нехватки памяти.
            with self.music_slots:
                try:
                    track, error = music.fetch(query)
                except Exception as exc:  # noqa: BLE001
                    log.warning("музыка: сбой поиска %s", exc)
                    track, error = None, "сбой поиска"
            if not track:
                if note:
                    self.tg.delete(chat_id, note.get("message_id"))
                if sure:
                    self.tg.send(chat_id, "Не нашла «%s». Напиши точнее: исполнителя и название."
                                 % query, reply_to=reply_to)
                else:
                    answer, delivered = self.answer(query, chat_id, user, reply_to=reply_to)
                    if not delivered:
                        self.deliver(chat_id, answer, reply_to=reply_to)
                return
            if note:
                self.tg.delete(chat_id, note.get("message_id"))
            caption = self.music_caption(track)
            try:
                self.tg.send_audio(chat_id, track["path"], title=track.get("title"),
                                   performer=track.get("artist"),
                                   duration=track.get("duration"),
                                   caption=caption, reply_to=reply_to,
                                   keyboard=keyboard, thumb=track.get("cover_path"))
            except Exception as exc:  # noqa: BLE001
                log.warning("музыка: не смог отправить файл (%s)", exc)
                self.tg.send(chat_id, "Файл скачался, но отправить не получилось: %s"
                             % music.describe(track), reply_to=reply_to)

        threading.Thread(target=work, daemon=True).start()
        return True

    def music_caption(self, track):
        lines = [music.describe(track)]
        if track.get("full"):
            lines.append("Полный трек.")
        else:
            lines.append("Полную версию сервер не отдаёт — это превью 30 секунд. "
                         "Напиши название иначе, попробую найти целиком.")
        if track.get("link"):
            lines.append(track["link"])
        return "\n".join(lines)

    def music_diag(self, chat_id):
        """Проверка источников — для владельца."""
        info = music.probe()
        lines = ["Поиск песен:",
                 "полные треки: %s" % ("да" if info.get("ytdlp") else "нет (доставлю превью)"),
                 "полноценный mp3: %s" % ("да" if info.get("ffmpeg") else "нет"),
                 "SoundCloud: %s" % ("ок" if info.get("soundcloud") else "не отвечает"),
                 "Deezer: %s" % ("ок" if info.get("deezer") else "не отвечает"),
                 "iTunes: %s" % ("ок" if info.get("itunes") else "не отвечает")]
        self.tg.send(chat_id, "\n".join(lines))

    # ------------------------------------------------------------------
    # кнопки
    # ------------------------------------------------------------------
    def _is_helper(self, uid, username=None):
        """Владелец и доверенный помощник: видят заявки и могут отвечать."""
        if uid in config.HELP_ADMINS:
            return True
        return self.db.is_admin(uid, username)

    def _private_ok(self, msg, user, chat_id):
        """Заявки принимаем только в личке."""
        chat_type = ((msg or {}).get("chat") or {}).get("type") or "private"
        if chat_type == "private":
            return True
        self.tg.send(chat_id, "%s, заявки и анкеты я принимаю в личке — напиши мне @%s"
                     % (user.get("first_name") or "Друг", config.BOT_USERNAME))
        return False

    def my_tickets_kb(self, uid):
        rows = []
        for row in self.db.user_tickets(uid, 3):
            if (row.get("status") or "new") in ("new", "taken"):
                rows.append([("Дополнить заявку #%d" % row["id"], "h:add:%d" % row["id"])])
        rows.append([("Новая заявка", "m:help")])
        rows.append([("В меню", "m:menu")])
        return keyboard(rows)

    def help_admin_button(self, data, chat_id, user, cbq):
        """Кнопки на карточке заявки: взять в работу, ответить, закрыть, профиль."""
        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        try:
            tid = int(parts[2])
        except (IndexError, ValueError):
            return
        if not self._is_helper(user.get("id"), user.get("username")):
            self.tg.send(chat_id, "Заявками занимается администрация сервера.")
            return
        ticket = self.db.ticket(tid)
        if not ticket:
            self.tg.send(chat_id, "Заявка #%d не найдена." % tid)
            return
        if action == "take":
            self.tg.send(chat_id, self.take_ticket(tid, user))
        elif action == "close":
            self.tg.send(chat_id, self.close_ticket_admin(tid, user))
        elif action == "ans":
            sent = self.tg.send(chat_id,
                                "Ответ по заявке #%d.\nОтветь реплаем на это сообщение — "
                                "текст уйдёт игроку. Или: /answer %d <текст>" % (tid, tid))
            if sent:
                self.db.link(chat_id, sent.get("message_id"), "ticket", tid)
        elif action == "who":
            info = self.db.user(ticket.get("user_id") or 0) or {}
            self.tg.send(chat_id,
                         "Игрок по заявке #%d\nИмя: %s\nЮзернейм: %s\nid: %s\n"
                         "Сообщений боту: %s\nЗаявок всего: %d"
                         % (tid, info.get("first_name") or "-",
                            ("@" + info["username"]) if info.get("username") else "-",
                            ticket.get("user_id"), info.get("msg_count") or 0,
                            len(self.db.user_tickets(ticket.get("user_id") or 0, 20))))

    def on_callback(self, cbq):
        data = cbq.get("data") or ""
        msg = cbq.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        user = cbq.get("from") or {}
        uid = user.get("id")
        self.tg.answer_callback(cbq.get("id"))
        self.db.touch_user(user, chat_id, is_admin=self.db.is_admin(uid, user.get("username")))
        if not data:
            return

        # кнопки рассылки
        if broadcast.handle_callback(self, data, chat_id, user, cbq):
            return

        if data == "m:menu":
            self.state.pop(uid, None)
            self.tg.send(chat_id, "Меню:", keyboard=MAIN_MENU)
        elif data == "m:plugins":
            self.tg.send(chat_id, self.plugins_text(),
                         keyboard=keyboard([[("Датапаки", "m:datapacks")],
                                            [("В меню", "m:menu")]]))
        elif data == "m:datapacks":
            self.tg.send(chat_id, self.datapacks_text(),
                         keyboard=keyboard([[("Плагины", "m:plugins")],
                                            [("В меню", "m:menu")]]))
        elif data == "m:donate":
            v = sinfo.values(self.db)
            self.tg.send(chat_id,
                         "Поддержка сервера %s\n"
                         "Донат-привилегии: %s\n"
                         "Финансовая поддержка: %s\n\n"
                         "Заявка на помощь и вопросы к администрации — кнопка «Нужна помощь»."
                         % (v["about"], v["donate"], v["support"]), keyboard=BACK_MENU)
        elif data in ("m:help", "m:support"):
            if not self._private_ok(msg, user, chat_id):
                return
            self.start_help(chat_id, uid)
        elif data == "m:mytickets":
            self.tg.send(chat_id, self.my_tickets_text(uid), keyboard=self.my_tickets_kb(uid))
        elif data.startswith("h:cat:"):
            if not self._private_ok(msg, user, chat_id):
                return
            self.start_help_form(chat_id, uid, data[6:])
        elif data.startswith("h:add:"):
            try:
                tid = int(data.split(":")[2])
            except (IndexError, ValueError):
                return
            ticket = self.db.ticket(tid)
            if not ticket or ticket.get("user_id") != uid:
                self.tg.send(chat_id, "Заявка не найдена.", keyboard=MAIN_MENU)
                return
            self.state[uid] = {"form": "help_add", "ticket": tid}
            self.tg.send(chat_id, "Напиши, что добавить к заявке #%d — я приложу к ней." % tid,
                         keyboard=keyboard([[("Отмена", "m:cancel")]]))
        elif data.startswith(("h:take:", "h:close:", "h:ans:", "h:who:")):
            self.help_admin_button(data, chat_id, user, cbq)
        elif data in ("m:complaint", "m:form"):
            if not self._private_ok(msg, user, chat_id):
                return
            self.start_form(chat_id, uid, data[2:])
        elif data == "m:cancel":
            self.state.pop(uid, None)
            self.tg.send(chat_id, "Отменено.", keyboard=MAIN_MENU)
        elif data == "m:status":
            self.tg.send(chat_id, self.server_text(force=True), keyboard=BACK_MENU)
        elif data == "m:about":
            self.tg.send(chat_id, sinfo.render(self.db) + "\n\n" + HELP_TEXT, keyboard=BACK_MENU)
        elif data.startswith("w:ok:") or data.startswith("w:no:"):
            if not self.db.is_admin(uid, user.get("username")):
                self.tg.answer_callback(cbq.get("id"), "Решение принимает администрация сервера.", True)
                return
            try:
                tid = int(data.split(":")[2])
            except (IndexError, ValueError):
                return
            if data.startswith("w:ok:"):
                self.tg.answer_callback(cbq.get("id"), "Принимаю заявку…")
                self.tg.send(chat_id, self.accept_ticket(tid, user))
            else:
                self.tg.answer_callback(cbq.get("id"), "Напиши причину ответом")
                sent = self.tg.send(chat_id, "Отклоняю заявку #%d.\n"
                                             "Ответь на это сообщение причиной — или напиши «нет», "
                                             "чтобы отклонить без причины." % tid)
                if sent:
                    self.db.link(chat_id, sent.get("message_id"), "reject:%d" % tid)
        elif data == "a:tickets" and self.db.is_admin(uid, user.get("username")):
            rows = self.db.open_tickets(10)
            if not rows:
                self.tg.send(chat_id, "Открытых заявок нет.")
            else:
                lines = ["#%d [%s] %s: %s" % (r["id"], r["kind"], r["username"] or r["user_id"],
                                              (r["body"] or "").replace("\n", " ")[:120]) for r in rows]
                self.tg.send(chat_id, "Открытые заявки:\n" + "\n".join(lines))

