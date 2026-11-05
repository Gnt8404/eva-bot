"""Минимальный клиент Telegram Bot API на стандартной библиотеке.

Без внешних зависимостей: long-polling, отправка/правка сообщений,
клавиатуры, отправка действия "печатает", ответ на callback.
"""
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid

log = logging.getLogger("tg")

LIMIT = 4000


class TgError(Exception):
    def __init__(self, method, description, code=0):
        super().__init__("%s: %s" % (method, description))
        self.method = method
        self.description = description
        self.code = code


class Tg:
    def __init__(self, token, api="https://api.telegram.org"):
        self.token = token
        self.base = "%s/bot%s/" % (api, token)
        self.me = {}

    # ---------- низкий уровень ----------
    def call(self, method, http_timeout=75, retries=3, **params):
        body = json.dumps(params, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base + method,
            data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "MC-Eva-Bot/1.0",
            },
        )
        last = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=http_timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                if payload.get("ok"):
                    return payload.get("result")
                code = payload.get("error_code", 0)
                desc = payload.get("description", "unknown error")
                if code == 429:
                    wait = int(payload.get("parameters", {}).get("retry_after", 3))
                    time.sleep(min(wait + 1, 30))
                    last = TgError(method, desc, code)
                    continue
                raise TgError(method, desc, code)
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", "replace")
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = {}
                code = payload.get("error_code", exc.code)
                desc = payload.get("description", raw[:300])
                if code == 429:
                    wait = int(payload.get("parameters", {}).get("retry_after", 3))
                    time.sleep(min(wait + 1, 30))
                    last = TgError(method, desc, code)
                    continue
                if code >= 500:
                    time.sleep(2 + attempt * 2)
                    last = TgError(method, desc, code)
                    continue
                raise TgError(method, desc, code)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
                time.sleep(1.5 + attempt * 2)
        if isinstance(last, TgError):
            raise last
        raise TgError(method, "network error: %s" % last)

    def get_me(self):
        self.me = self.call("getMe") or {}
        return self.me

    # ---------- отправка файлов ----------
    def call_file(self, method, fields, files, http_timeout=180):
        """Отправка файла: multipart/form-data (аудио, документ, фото)."""
        boundary = "----EvaBoundary%s" % uuid.uuid4().hex
        body = bytearray()
        for key, value in fields.items():
            if value in (None, ""):
                continue
            body += ("--%s\r\n" % boundary).encode()
            body += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % key).encode()
            body += str(value).encode("utf-8") + b"\r\n"
        for key, (filename, blob, ctype) in files.items():
            if not blob:
                continue
            body += ("--%s\r\n" % boundary).encode()
            body += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                     % (key, filename)).encode("utf-8")
            body += ("Content-Type: %s\r\n\r\n" % ctype).encode()
            body += blob + b"\r\n"
        body += ("--%s--\r\n" % boundary).encode()
        req = urllib.request.Request(
            self.base + method,
            data=bytes(body),
            headers={
                "Content-Type": "multipart/form-data; boundary=%s" % boundary,
                "User-Agent": "MC-Eva-Bot/1.0",
            },
        )
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=http_timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                if payload.get("ok"):
                    return payload.get("result")
                last = TgError(method, payload.get("description", "unknown"),
                               payload.get("error_code", 0))
                if last.code == 429:
                    time.sleep(5)
                    continue
                raise last
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", "replace")
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = {}
                last = TgError(method, payload.get("description", raw[:200]),
                               payload.get("error_code", exc.code))
                if last.code == 429 or last.code >= 500:
                    time.sleep(3 + attempt * 3)
                    continue
                raise last
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
                time.sleep(2 + attempt * 2)
        if isinstance(last, TgError):
            raise last
        raise TgError(method, "network error: %s" % last)

    def send_audio(self, chat_id, path, title=None, performer=None, duration=None,
                   caption=None, reply_to=None, keyboard=None, thumb=None):
        """Отправляет музыкальный файл — играется в фоне прямо в Telegram."""
        filename = os.path.basename(path)
        with open(path, "rb") as fh:
            blob = fh.read()
        ext = os.path.splitext(filename)[1].lstrip(".").lower()
        ctype = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "aac": "audio/aac",
                 "opus": "audio/ogg", "ogg": "audio/ogg", "webm": "audio/webm",
                 "wav": "audio/wav"}.get(ext, "audio/mpeg")
        fields = {
            "chat_id": chat_id,
            "title": title,
            "performer": performer,
            "duration": int(duration) if duration else None,
            "caption": (caption or "")[:1000],
            "reply_to_message_id": reply_to,
            "allow_sending_without_reply": True if reply_to else None,
        }
        if keyboard is not None:
            fields["reply_markup"] = json.dumps(keyboard, ensure_ascii=False)
        files = {"audio": (filename, blob, ctype)}
        if thumb and os.path.exists(thumb):
            with open(thumb, "rb") as fh:
                files["thumbnail"] = ("cover.jpg", fh.read(), "image/jpeg")
            try:
                return self.call_file("sendAudio", fields, files)
            except TgError as exc:
                log.info("обложка не принята (%s), отправляю без неё", exc.description[:60])
                files.pop("thumbnail", None)
        return self.call_file("sendAudio", fields, files)

    def send_document(self, chat_id, path, caption=None, reply_to=None, keyboard=None):
        filename = os.path.basename(path)
        with open(path, "rb") as fh:
            blob = fh.read()
        fields = {"chat_id": chat_id, "caption": (caption or "")[:1000],
                  "reply_to_message_id": reply_to}
        if keyboard is not None:
            fields["reply_markup"] = json.dumps(keyboard, ensure_ascii=False)
        return self.call_file("sendDocument", fields,
                              {"document": (filename, blob, "application/octet-stream")})

    def action(self, chat_id, name="typing"):
        try:
            self.call("sendChatAction", chat_id=chat_id, action=name, retries=1)
        except Exception:
            pass

    def get_updates(self, offset, timeout=45):
        try:
            res = self.call(
                "getUpdates",
                http_timeout=timeout + 20,
                offset=offset,
                timeout=timeout,
                allowed_updates=["message", "edited_message", "callback_query", "channel_post",
                             "my_chat_member"],
            )
            return res or []
        except TgError as exc:
            if "timeout" in str(exc).lower():
                return []
            log.warning("getUpdates: %s", exc)
            time.sleep(3)
            return []

    # ---------- высокий уровень ----------
    def send(self, chat_id, text, keyboard=None, reply_to=None, preview=False,
             markdown=False, html=False):
        return self.send_message(chat_id, text, keyboard=keyboard, reply_to=reply_to,
                                 preview=preview, markdown=markdown, html=html)

    def send_message(self, chat_id, text, keyboard=None, reply_to=None, preview=False,
                     markdown=False, html=False):
        text = text if text and text.strip() else "(пусто)"
        parts = _split(text)
        result = None
        for i, chunk in enumerate(parts):
            params = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": not preview,
            }
            if keyboard is not None and i == len(parts) - 1:
                params["reply_markup"] = keyboard
            if reply_to and i == 0:
                params["reply_to_message_id"] = reply_to
                params["allow_sending_without_reply"] = True
            if markdown:
                params["parse_mode"] = "Markdown"
            elif html:
                params["parse_mode"] = "HTML"
            result = self.call("sendMessage", **params)
        return result

    def edit(self, chat_id, message_id, text, keyboard=None, markdown=False, html=False):
        text = text if text and text.strip() else "(пусто)"
        params = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": _split(text)[0],
            "disable_web_page_preview": True,
        }
        if keyboard is not None:
            params["reply_markup"] = keyboard
        if markdown:
            params["parse_mode"] = "Markdown"
        elif html:
            params["parse_mode"] = "HTML"
        try:
            return self.call("editMessageText", **params)
        except TgError as exc:
            if "not modified" in str(exc).lower():
                return None
            raise

    def send_html(self, chat_id, text, keyboard=None, reply_to=None, preview=False):
        """Отправка с HTML-разметкой и откатом на чистый текст."""
        try:
            return self.send_message(chat_id, text, keyboard=keyboard, reply_to=reply_to,
                                     preview=preview, html=True)
        except TgError as exc:
            log.warning("html send failed: %s", exc)
            return self.send_message(chat_id, strip_tags(text), keyboard=keyboard,
                                     reply_to=reply_to, preview=preview)

    def typing(self, chat_id):
        try:
            self.call("sendChatAction", chat_id=chat_id, action="typing", retries=1)
        except Exception:
            pass

    def answer_callback(self, callback_id, text=None, alert=False):
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_id,
                      text=text, show_alert=alert, retries=1)
        except Exception:
            pass

    def delete(self, chat_id, message_id):
        try:
            return self.call("deleteMessage", chat_id=chat_id, message_id=message_id, retries=1)
        except Exception:
            return None

    def get_chat_member(self, chat_id, user_id):
        try:
            return self.call("getChatMember", chat_id=chat_id, user_id=user_id, retries=1)
        except Exception:
            return None

    def set_my_commands(self, commands, chat_id=None):
        """Показывает список команд в меню Telegram. commands = [(команда, описание)].

        chat_id — показать список только в личке конкретного человека.
        """
        params = {"commands": [{"command": name, "description": text}
                               for name, text in commands]}
        if chat_id is not None:
            params["scope"] = {"type": "chat", "chat_id": chat_id}
        try:
            return self.call("setMyCommands", retries=1, **params)
        except Exception as exc:
            log.info("меню команд не обновилось: %s", exc)
            return None


class Typing(threading.Thread):
    """Держит статус "печатает..." пока идёт генерация ответа."""

    def __init__(self, tg, chat_id, interval=4.0):
        super().__init__(daemon=True)
        self.tg = tg
        self.chat_id = chat_id
        self.interval = interval
        self._stop = threading.Event()

    def run(self):
        self.tg.typing(self.chat_id)
        while not self._stop.wait(self.interval):
            self.tg.typing(self.chat_id)

    def stop(self):
        self._stop.set()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False


def _split(text, limit=LIMIT):
    text = str(text)
    if len(text) <= limit:
        return [text]
    chunks = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    if rest:
        chunks.append(rest)
    return chunks


def keyboard(rows):
    """rows = [[("текст", "callback"), ("текст", "url", "https://..."), ...], ...]"""
    out = []
    for row in rows:
        line = []
        for item in row:
            if len(item) == 3:
                text, kind, value = item
                if kind == "url":
                    line.append({"text": text, "url": value})
                elif kind == "web_app":
                    line.append({"text": text, "web_app": {"url": value}})
                else:
                    line.append({"text": text, "callback_data": value})
            else:
                text, data = item
                line.append({"text": text, "callback_data": data})
        out.append(line)
    return {"inline_keyboard": out}


def reply_keyboard(rows, resize=True):
    return {
        "keyboard": [[{"text": t} for t in row] for row in rows],
        "resize_keyboard": resize,
    }


def esc(text):
    """Экранирование для parse_mode=HTML."""
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def strip_tags(text):
    import re as _re
    return _re.sub(r"<[^>]+>", "", str(text or ""))


def mention(user):
    """Безопасное упоминание: @username или ссылка по id."""
    user = user or {}
    name = user.get("username")
    if name:
        return "@" + name
    title = " ".join(x for x in [user.get("first_name"), user.get("last_name")] if x)
    if user.get("id"):
        return '<a href="tg://user?id=%s">%s</a>' % (user["id"], esc(title or user["id"]))
    return esc(title or "неизвестный")
