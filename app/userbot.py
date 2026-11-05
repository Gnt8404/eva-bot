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
import os
import threading
from collections import deque
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
    def __init__(self, bot, owner_uid=None):
        self.bot = bot
        self.store = bot.db
        self.owner_uid = int(owner_uid or config.SUPER_ADMIN)
        self.settings_file = "/bot-data/userbots/%s.json" % self.owner_uid
        self.session_file = "/bot-data/userbots/%s.session" % self.owner_uid
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
        self._last_reply = {}
        self._spam = {}
        self._global_replies = deque(maxlen=200)
        self.antispam = True
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
            self.session = (persist.read_text(self.store, self.session_file) or "").strip()
            if not self.api_id:
                self.api_id = int(self.settings.get("api_id")
                                  or getattr(config, "TELEGRAM_API_ID", 0) or 0)
            if not self.api_hash:
                self.api_hash = (self.settings.get("api_hash")
                                 or getattr(config, "TELEGRAM_API_HASH", ""))
            self.enabled = self.settings.get("enabled", "on") != "off"
            self.autoreply = self.settings.get("autoreply", "on") != "off"
            self.antispam = self.settings.get("antispam", "on") != "off"
        except Exception as exc:  # noqa: BLE001
            log.info("userbot настройки не загрузились: %s", exc)
        if self.session and self.api_id and self.api_hash and self.enabled:
            self.status = "starting"
            # Пауза, чтобы старый процесс при деплое успел умереть: иначе
            # Telegram отзывает auth key (AuthKeyUnregistered) из-за двух клиентов.
            time.sleep(45)
            self._boot()

    def _load_settings(self):
        raw = persist.read_text(self.store, self.settings_file)
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
                "autoreply": "on" if self.autoreply else "off",
                "antispam": "on" if self.antispam else "off"}
        self.settings.update(data)
        persist.write_text(self.store, self.settings_file,
                           json.dumps(data, ensure_ascii=False))
        if self.session:
            persist.write_text(self.store, self.session_file, self.session)

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
                                self.api_hash, device_model="Desktop",
                                system_version="Windows 10", app_version="4.16.8")
        await client.connect()
        return client

    def _boot(self):
        try:
            self._run(self._connect(), timeout=60)
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.error = str(exc)[:300]
            log.info("userbot не подключился: %s", exc)
            err = str(exc)
            if "AuthKey" in err or "not logged in" in err.lower():
                try:
                    self.bot.tg.send(self.owner_uid,
                        "Сессия аккаунта слетела (Telegram отозвал ключ). "
                        "Подключи заново: /connect")
                except Exception:
                    pass

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
        """Автоответчик в личке на ЛИЧНОМ аккаунте (без команд)."""
        if self.status != "connected":
            return
        try:
            if not event.is_private:
                return
            sender = await event.get_sender()
            if sender is None or getattr(sender, "bot", False):
                return
            me_id = getattr(self.me, "id", None)
            sender_id = getattr(sender, "id", None)
            if me_id and sender_id == me_id:
                return
            text = (event.raw_text or "").strip()
            if not text or not self.autoreply:
                return
            now = time.time()
            last = self._last_reply.get(sender_id, 0)
            if now - last < 15:          # не чаще одного сообщения в 15 секунд
                return
            if self.antispam and not self._spam_allowed(sender_id, text, now):
                log.info("userbot антиспам: пропуск для %s", sender_id)
                return
            reply = autopost.reply_text(self.store, text)
            if not reply:
                return
            await event.reply(reply)
            self._last_reply[sender_id] = now
            self._remember_reply(sender_id, text, now)
            log.info("userbot автоответ для %s (%s)", sender_id,
                     getattr(sender, "username", ""))
            try:
                from . import traces
                traces.trace({"kind": "userbot_autoreply", "uid": sender_id,
                              "text": (text or "")[:200]})
            except Exception:
                pass
        except Exception as exc:  # noqa: BLE001
            log.info("userbot автоответ не отправлен: %s", exc)

    def _spam_allowed(self, sender_id, text, now):
        """Лимиты: 1/15с, 3/мин, 10/час, 30/сутки; повторы — раз в 10 минут."""
        rec = self._spam.setdefault(sender_id, {"times": deque(maxlen=50),
                                                "texts": {}})
        times = rec["times"]
        while times and now - times[0] > 86400:
            times.popleft()
        if len(times) >= 30:
            return False
        if sum(1 for t in times if now - t < 60) >= 3:
            return False
        if sum(1 for t in times if now - t < 3600) >= 10:
            return False
        norm = " ".join((text or "").lower().split())[:300]
        texts = rec["texts"]
        for key, ts in list(texts.items()):
            if now - ts > 600:
                texts.pop(key, None)
        if norm and norm in texts:
            return False
        # общий предохранитель: не больше 20 ответов в минуту со всех чатов
        while self._global_replies and now - self._global_replies[0] > 60:
            self._global_replies.popleft()
        if len(self._global_replies) >= 20:
            return False
        return True

    def _remember_reply(self, sender_id, text, now):
        rec = self._spam.setdefault(sender_id, {"times": deque(maxlen=50),
                                                "texts": {}})
        rec["times"].append(now)
        norm = " ".join((text or "").lower().split())[:300]
        if norm:
            rec["texts"][norm] = now
        self._global_replies.append(now)

    async def _send_gif(self, event, text):
        """Сделать простую анимированную GIF с текстом и отправить."""
        try:
            from PIL import Image, ImageDraw, ImageFont
            text = (text or "Eva")[:24]
            frames = []
            for step in range(10):
                img = Image.new("RGB", (480, 270), (18, 22, 34))
                draw = ImageDraw.Draw(img)
                try:
                    font = ImageFont.truetype("arial.ttf", 44)
                except Exception:
                    font = ImageFont.load_default()
                x = 20 + step * 12
                draw.text((x, 100), text, fill=(230, 236, 255), font=font)
                draw.text((20, 220), "«Eva ии помощница»", fill=(120, 140, 180),
                          font=ImageFont.load_default())
                frames.append(img)
            path = os.path.join(config.DATA_DIR, "userbot_gif_%s.gif" % self.owner_uid)
            frames[0].save(path, save_all=True, append_images=frames[1:],
                           duration=90, loop=0)
            await event.reply(file=path, message="")
            try:
                os.remove(path)
            except OSError:
                pass
        except Exception as exc:  # noqa: BLE001
            log.info("gif не сделалась: %s", exc)
            await event.reply("Не смогла сделать гифку: %s" % str(exc)[:100])

    async def _pin_notice(self, client):
        try:
            msg = await client.send_message("me", "Ева подключена к аккаунту.\n"
                                                 "Автоответы в личке включены.")
            await client.pin_message("me", msg, notify=False)
        except Exception as exc:  # noqa: BLE001
            log.info("закреп не поставился: %s", exc)

    # ---------------------------------------------------------------- login
    def _make_qr(self, url):
        import qrcode
        path = os.path.join(config.DATA_DIR, "userbot_qr.png")
        img = qrcode.make(url)
        img.save(path)
        return path

    def start_qr(self, chat_id):
        if not HAS_TELETHON:
            return False, "Telethon не установлен."
        if not self.api_id or not self.api_hash:
            return False, "Сначала /ub api <id> <hash>."
        if self.status == "connected":
            return False, "Аккаунт уже подключён. Статус: /connect status"
        self._ensure_loop()
        self._qr_future = asyncio.run_coroutine_threadsafe(self._qr_flow(chat_id, chat_id),
                                                           self.loop)
        return True, "Готовлю QR-код для входа..."

    async def _qr_flow(self, notify_chat, chat_id):
        try:
            # Новый QR-вход всегда создаёт свежую сессию: старый auth key мог
            # быть отозван Telegram (AuthKeyUnregistered).
            client = TelegramClient(StringSession(""), self.api_id,
                                    self.api_hash, device_model="Desktop",
                                    system_version="Windows 10",
                                    app_version="4.16.8")
            await client.connect()
            if await client.is_user_authorized():
                self.client = client
                self._bind(client)
                self.me = await client.get_me()
                self.status = "connected"
                self._save_settings()
                return
            self.status = "await_qr"
            for _attempt in range(4):
                qr = await client.qr_login()
                try:
                    path = self._make_qr(qr.url)
                    self.bot.tg.send_document(
                        notify_chat, path,
                        caption="QR для входа в твой аккаунт.\n"
                                "Открой Telegram на телефоне: Настройки → Устройства → "
                                "Подключить устройство → сканируй QR.\n"
                                "QR живёт ~30 секунд, я пришлю новый при необходимости.")
                except Exception as exc:  # noqa: BLE001
                    log.info("QR не отправился: %s", exc)
                    self.bot.tg.send(notify_chat, "QR: %s" % qr.url)
                try:
                    await qr.wait(timeout=90)
                    break
                except asyncio.TimeoutError:
                    continue
                except SessionPasswordNeededError:
                    self.status = "await_password"
                    self._pw_q = asyncio.Queue()
                    self.bot.tg.send(notify_chat,
                                     "Аккаунт защищён паролем (2FA).\n"
                                     "Пришли: /password <пароль> — сообщение удалю.")
                    password = await self._pw_q.get()
                    await client.sign_in(password=password)
                    break
            if not await client.is_user_authorized():
                self.status = "error"
                self.error = "QR не подтверждён"
                self.bot.tg.send(notify_chat, "Вход по QR не завершён. Попробуй /ub qr ещё раз.")
                return
            self.client = client
            self.session = StringSession.save(client.session)
            self._bind(client)
            self.me = await client.get_me()
            self.status = "connected"
            self._save_settings()
            await self._pin_notice(client)
            sent = self.bot.tg.send(notify_chat, "Ева подключена к аккаунту @%s.\n"
                                               "Автоответы в личке включены.\n"
                                               "Отключить: /connect off"
                             % getattr(self.me, "username", self.me.id))
            try:
                if sent:
                    self.bot.tg.call("pinChatMessage", chat_id=notify_chat,
                                     message_id=sent.get("message_id"),
                                     disable_notification=True)
            except Exception as exc:  # noqa: BLE001
                log.info("закреп в чате бота не удался: %s", exc)
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.error = str(exc)[:300]
            log.info("QR-вход не удался: %s", exc)
            self.bot.tg.send(notify_chat, "Ошибка QR-входа: %s" % self.error)

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
                                    self.api_hash, device_model="Desktop",
                                    system_version="Windows 10", app_version="4.16.8")
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

    def disconnect(self):
        """Отключить клиент, оставив сессию для повторного включения."""
        self.enabled = False
        try:
            if self.client is not None:
                self._run(self.client.disconnect(), timeout=15)
        except Exception as exc:  # noqa: BLE001
            log.info("userbot disconnect: %s", exc)
        self.client = None
        self.status = "off"
        self.me = None
        self._save_settings()
        return True

    def logout(self):
        """Выйти из аккаунта: Telegram-сессия удаляется безвозвратно."""
        try:
            if self.client is not None:
                self._run(self.client.log_out(), timeout=20)
        except Exception as exc:  # noqa: BLE001
            log.info("userbot logout: %s", exc)
        try:
            if self.client is not None:
                self._run(self.client.disconnect(), timeout=10)
        except Exception:
            pass
        self.client = None
        self.session = ""
        self.status = "off"
        self.me = None
        self.enabled = False
        self._save_settings()
        persist.write_text(self.store, self.session_file, "")
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
        lines.append("Автоответы в личке: %s" % ("вкл" if self.autoreply else "выкл"))
        lines.append("Антиспам: %s" % ("вкл" if self.antispam else "выкл"))
        try:
            text = (self.store.get("autoreply_default")
                    or getattr(config, "AUTOREPLY_DEFAULT_TEXT", ""))
            if text:
                lines.append("Текст: %s" % text[:100])
        except Exception:
            pass
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
    if sub in ("qr", "куар", "qrlogin"):
        ok, message = ub.start_qr(chat_id)
        bot.tg.send(chat_id, message)
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
        try:
            bot.tg.delete(chat_id, msg.get("message_id"))
        except Exception:
            pass
        bot.tg.send(chat_id, message)
        return True
    if sub in ("on", "вкл", "включить"):
        ub.enabled = True
        ub._save_settings()
        if ub.session:
            threading.Thread(target=ub._boot, daemon=True).start()
        bot.tg.send(chat_id, "Userbot включён.")
        return True
    if sub in ("disconnect", "отключить"):
        ub.disconnect()
        bot.tg.send(chat_id, "Аккаунт отключён (сессия сохранена).")
        return True
    if sub in ("off", "выкл", "выключить"):
        ub.disconnect()
        bot.tg.send(chat_id, "Userbot выключен.")
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
