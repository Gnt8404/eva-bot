"""Userbot на Telethon: вход в личный Telegram-аккаунт и автоответы от его имени.

Управление — только владелец, команды в личке с ботом:
    /ub status
    /ub api <api_id> <api_hash>     (создать приложение на my.telegram.org)
    /ub login <номер>               (придёт код в Telegram)
    /ub code <код>
    /ub password <пароль 2FA>
    /ub on | /ub off
    /ub autoreply on | off
    /ub text <текст по умолчанию>
    /ub send <@чат|id> <текст>       отправить от имени аккаунта
    /ub logout

Сессия и настройки сохраняются в /bot-data/userbot.json и
/bot-data/userbot.session на диске сервера (через API панели), поэтому
переживают перезапуск облака. api_id/api_hash можно также задать
переменными TELEGRAM_API_ID / TELEGRAM_API_HASH.
"""
import asyncio
import json
import logging
import threading
import time

try:
    from telethon import TelegramClient, events
    from telethon.errors import SessionPasswordNeededError
    from telethon.sessions import StringSession
    HAS_TELETHON = True
except Exception:  # noqa: BLE001
    HAS_TELETHON = False

from . import autopost, config, persist

log = logging.getLogger("userbot")

SETTINGS_FILE = "/bot-data/userbot.json"
SESSION_FILE = "/bot-data/userbot.session"
COMMANDS = {"/ub", "/userbot"}


class Userbot:
    def __init__(self, bot):
        self.bot = bot
        self.store = bot.db
        self.loop = None
        self.thread = None
        self.client = None
        self.status = "off"
        self.me = None
        self.phone = ""
        self.error = ""
        self.autoreply = True
        self._code_q = None
        self._pw_q = None
        self._lock = threading.RLock()
        self.settings = {}
        self.session = ""
        self.api_id = getattr(config, "TELEGRAM_API_ID", 0) or 0
        self.api_hash = getattr(config, "TELEGRAM_API_HASH", "") or ""
        self.enabled = True
        self.autoreply = True
        if not HAS_TELETHON:
            self.status = "no-telethon"
        else:
            threading.Thread(target=self._lazy_init, daemon=True,
                             name="userbot-init").start()

    # ---------------------------------------------------------------- setup
    def _lazy_init(self):
        try:
            self.settings = self._load_settings()
            self.session = (persist.read_text(self.store, SESSION_FILE) or "").strip()
            if not self.api_id:
                self.api_id = int(self.settings.get("api_id")
                                  or getattr(config, "TELEGRAM_API_ID", 0) or 0)
            if not self.api_hash:
                self.api_hash = (self.settings.get("api_hash")
                                 or getattr(config, "TELEGRAM_API_HASH", ""))
            self.enabled = self.settings.get("enabled", "on") != "off"
            self.autoreply = self.settings.get("autoreply", "on") != "off"
        except Exception as exc:  # noqa: BLE001
            log.info("userbot настройки не загрузились: %s", exc)
        if self.session and self.api_id and self.api_hash and self.enabled:
            self.status = "starting"
            self._boot()

    def _load_settings(self):
        raw = persist.read_text(self.store, SETTINGS_FILE)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    def _save_settings(self):
        data = {"api_id": self.api_id, "api_hash": self.api_hash,
                "phone": self.phone, "enabled": "on" if self.enabled else "off",
                "autoreply": "on" if self.autoreply else "off"}
        self.settings.update(data)
        persist.write_text(self.store, SETTINGS_FILE,
                           json.dumps(data, ensure_ascii=False))
        if self.session:
            persist.write_text(self.store, SESSION_FILE, self.session)

    # ---------------------------------------------------------------- loop
    def _ensure_loop(self):
        with self._lock:
            if self.loop and self.thread and self.thread.is_alive():
                return self.loop
            self.loop = asyncio.new_event_loop()
            self.thread = threading.Thread(target=self._loop_main, daemon=True,
                                           name="userbot-loop")
            self.thread.start()
            return self.loop

    def _loop_main(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _run(self, coro, timeout=40):
        loop = self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    # ---------------------------------------------------------------- client
    async def _make_client(self):
        client = TelegramClient(StringSession(self.session), self.api_id,
                                self.api_hash, device_model="Eva bot",
                                system_version="Windows 10", app_version="6.9")
        await client.connect()
        return client

    def _boot(self):
        try:
            self._run(self._connect(), timeout=60)
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.error = str(exc)[:300]
            log.info("userbot не подключился: %s", exc)

    async def _connect(self):
        try:
            client = await self._make_client()
            if not await client.is_user_authorized():
                self.status = "unauthorized"
                await client.disconnect()
                return
            self.client = client
            self._bind(client)
            me = await client.get_me()
            self.me = me
            self.status = "connected"
            log.info("userbot подключён как @%s", getattr(me, "username", me.id))
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.error = str(exc)[:300]
            raise

    def _bind(self, client):
        client.add_event_handler(self._on_new_message, events.NewMessage(incoming=True))

    async def _on_new_message(self, event):
        if not self.autoreply or self.status != "connected":
            return
        try:
            if not event.is_private:
                return
            sender = await event.get_sender()
            if sender is None or getattr(sender, "bot", False):
                return
            me_id = getattr(self.me, "id", None)
            if me_id and getattr(sender, "id", None) == me_id:
                return
            text = event.raw_text or ""
            reply = autopost.match_reply(self.store, text)
            if not reply:
                return
            await event.reply(reply)
            log.info("userbot автоответ для %s", getattr(sender, "id", "?"))
        except Exception as exc:  # noqa: BLE001
            log.info("userbot автоответ не отправлен: %s", exc)

    # ---------------------------------------------------------------- login
    def start_login(self, phone):
        if not HAS_TELETHON:
            return False, "Telethon не установлен на сервере."
        if not self.api_id or not self.api_hash:
            return False, "Сначала задай api_id/api_hash: /ub api <id> <hash>"
        self.phone = phone
        self.status = "await_code"
        self._ensure_loop()

        async def flow():
            client = TelegramClient(StringSession(self.session), self.api_id,
                                    self.api_hash, device_model="Eva bot",
                                    system_version="Windows 10", app_version="6.9")
            await client.connect()
            if await client.is_user_authorized():
                self.client = client
                self._bind(client)
                self.me = await client.get_me()
                self.status = "connected"
                self._save_settings()
                return "already"
            await client.send_code_request(phone)
            self._code_q = asyncio.Queue()
            self.status = "await_code"
            code = await self._code_q.get()
            try:
                await client.sign_in(phone=phone, code=code)
            except SessionPasswordNeededError:
                self._pw_q = asyncio.Queue()
                self.status = "await_password"
                password = await self._pw_q.get()
                await client.sign_in(password=password)
            if not await client.is_user_authorized():
                return "failed"
            self.client = client
            self.session = StringSession.save(client.session)
            self._bind(client)
            self.me = await client.get_me()
            self.status = "connected"
            self._save_settings()
            return "ok"

        self._login_future = asyncio.run_coroutine_threadsafe(flow(), self.loop)
        return True, "Код отправлен в Telegram. Введи его: /ub code <код>"

    def submit_code(self, code):
        if not self._code_q:
            return False, "Сейчас вход не ожидает код."
        self.loop.call_soon_threadsafe(self._code_q.put_nowait, code)
        return True, "Код принят, проверяю."

    def submit_password(self, password):
        if not self._pw_q:
            return False, "Сейчас вход не ожидает пароль 2FA."
        self.loop.call_soon_threadsafe(self._pw_q.put_nowait, password)
        return True, "Пароль принят."

    def logout(self):
        try:
            if self.client is not None:
                self._run(self.client.log_out(), timeout=20)
        except Exception as exc:  # noqa: BLE001
            log.info("userbot logout: %s", exc)
        self.client = None
        self.session = ""
        self.status = "off"
        self.me = None
        self._save_settings()
        persist.write_text(self.store, SESSION_FILE, "")
        return True

    def send(self, target, text):
        if self.status != "connected" or self.client is None:
            return False, "Userbot не подключён (%s)." % self.status
        try:
            async def job():
                return await self.client.send_message(target, text)
            self._run(job(), timeout=45)
            return True, "Отправлено от имени аккаунта."
        except Exception as exc:  # noqa: BLE001
            return False, "Не отправлено: %s" % str(exc)[:300]

    def status_text(self):
        lines = ["Userbot (личный аккаунт)"]
        if not HAS_TELETHON:
            lines.append("Telethon не установлен — нужен pip install telethon.")
            return "\n".join(lines)
        state = {"connected": "подключён", "await_code": "ждёт код",
                 "await_password": "ждёт пароль 2FA", "unauthorized": "не авторизован",
                 "off": "выключен", "starting": "запускается",
                 "error": "ошибка"}.get(self.status, self.status)
        lines.append("Статус: %s" % state)
        if self.me:
            lines.append("Аккаунт: %s (@%s)" % (
                getattr(self.me, "first_name", ""),
                getattr(self.me, "username", self.me.id)))
        lines.append("api_id: %s" % ("есть" if self.api_id else "нет"))
        lines.append("Автоответы: %s" % ("вкл" if self.autoreply else "выкл"))
        if self.error:
            lines.append("Ошибка: %s" % self.error)
        lines.append("")
        lines.append("Вход: /ub api <id> <hash> -> /ub login <номер> -> /ub code <код>")
        return "\n".join(lines)


# ------------------------------------------------------------------ команды
def is_command(cmd):
    return cmd in COMMANDS


def handle_command(bot, msg, text, chat_id, user, arg):
    ub = getattr(bot, "userbot", None)
    if ub is None:
        bot.tg.send(chat_id, "Userbot не инициализирован.")
        return True
    uid = user.get("id")
    if uid != config.SUPER_ADMIN:
        bot.tg.send(chat_id, "Userbot — только для владельца.")
        return True
    parts = (arg or "").strip().split(None, 1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub in ("", "status", "статус"):
        bot.tg.send(chat_id, ub.status_text())
        return True
    if sub in ("api", "апи"):
        chunks = rest.split()
        if len(chunks) < 2 or not chunks[0].isdigit():
            bot.tg.send(chat_id, "Напиши: /ub api <api_id> <api_hash> (my.telegram.org)")
            return True
        ub.api_id = int(chunks[0])
        ub.api_hash = chunks[1]
        ub.settings.update({"api_id": ub.api_id, "api_hash": ub.api_hash})
        ub._save_settings()
        try:
            bot.tg.delete(chat_id, msg.get("message_id"))
        except Exception:
            pass
        bot.tg.send(chat_id, "api_id/api_hash сохранены. Теперь: /ub login <номер>")
        return True
    if sub in ("login", "вход", "войти"):
        phone = rest.replace(" ", "")
        if not phone:
            bot.tg.send(chat_id, "Напиши: /ub login +79991234567")
            return True
        ok, message = ub.start_login(phone)
        bot.tg.send(chat_id, message)
        return True
    if sub == "code":
        ok, message = ub.submit_code(rest.strip())
        bot.tg.send(chat_id, message)
        return True
    if sub in ("password", "пароль", "2fa"):
        ok, message = ub.submit_password(rest.strip())
        bot.tg.send(chat_id, message)
        return True
    if sub in ("on", "вкл", "включить"):
        ub.enabled = True
        ub._save_settings()
        if ub.session:
            threading.Thread(target=ub._boot, daemon=True).start()
        bot.tg.send(chat_id, "Userbot включён.")
        return True
    if sub in ("off", "выкл", "выключить"):
        ub.enabled = False
        ub._save_settings()
        bot.tg.send(chat_id, "Userbot выключен (сессия сохранена).")
        return True
    if sub in ("autoreply", "автоответы"):
        if rest.lower() in ("on", "вкл", "включить"):
            ub.autoreply = True
        elif rest.lower() in ("off", "выкл", "выключить"):
            ub.autoreply = False
        else:
            bot.tg.send(chat_id, "Напиши: /ub autoreply on|off")
            return True
        ub._save_settings()
        bot.tg.send(chat_id, "Автоответы userbot: %s" % ("вкл" if ub.autoreply else "выкл"))
        return True
    if sub in ("text", "текст"):
        if not rest:
            bot.tg.send(chat_id, "Напиши: /ub text <текст ответа по умолчанию>")
            return True
        bot.db.set("autoreply_default", rest[:1500])
        bot.tg.send(chat_id, "Текст автоответа сохранён (с подписью).")
        return True
    if sub in ("send", "отправить"):
        chunks = rest.split(None, 1)
        if len(chunks) < 2:
            bot.tg.send(chat_id, "Напиши: /ub send @чат <текст> или /ub send -100123 <текст>")
            return True
        target = chunks[0]
        if target.lstrip("-").isdigit():
            target = int(target)
        ok, message = ub.send(target, chunks[1])
        bot.tg.send(chat_id, message)
        return True
    if sub in ("logout", "выход"):
        ub.logout()
        bot.tg.send(chat_id, "Userbot отключён, сессия удалена.")
        return True
    bot.tg.send(chat_id, "Команды: status | api | login | code | password | on | off | "
                         "autoreply | text | send | logout")
    return True
