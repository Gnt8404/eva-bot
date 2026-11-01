"""Плавная «печать» ответа: текст появляется у человека на глазах.

Telegram не умеет стримить сообщения, поэтому печать сделана правкой одного и того
же сообщения: сначала уходит первая часть, дальше бот через равные промежутки
дописывает текст. Скорость, интервал и предельная длительность задаются в
app/config.py (TYPE_*).

Использование:
    tw = Typewriter(tg, chat_id, reply_to=..., keyboard=..., group=False)
    text = ai.chat(messages, on_delta=tw.feed)   # можно кормить по кускам
    tw.finish(text)                              # доводит текст до конца

Если печать отключена (TYPE_ANIMATION=off) или сообщение слишком длинное,
Typewriter просто отправляет текст нормальным сообщением.
"""
import logging
import threading
import time

from . import config

log = logging.getLogger("typer")


class Typewriter:
    """Дописывает сообщение, пока модель генерирует ответ."""

    def __init__(self, tg, chat_id, reply_to=None, keyboard=None, group=False,
                 animate=None):
        self.tg = tg
        self.chat_id = chat_id
        self.reply_to = reply_to
        self.keyboard = keyboard
        self.group = bool(group)
        self.animate = bool(config.TYPE_ANIMATION if animate is None else animate)
        self.interval = config.TYPE_GROUP_INTERVAL if self.group else config.TYPE_INTERVAL
        self.cps = config.TYPE_GROUP_CPS if self.group else config.TYPE_CPS
        self.max_sec = config.TYPE_MAX_SEC
        self.min_chars = config.TYPE_MIN_CHARS

        self._cv = threading.Condition()
        self._text = ""
        self._done = False
        self._thread = None
        self._message_id = None
        self._shown = 0
        self._started = time.time()
        self._last_edit = 0.0
        self._failed = False

    # ---------------------------------------------------------------- вход
    def feed(self, text):
        """Накопленный текст от модели (вызывается на каждом куске стрима)."""
        with self._cv:
            self._text = text or ""
            self._cv.notify_all()
            start = self._thread is None
        if start:
            self._start_thread()

    def finish(self, text=None, keyboard=None):
        """Текст готов. Доводит печать до конца и возвращает id сообщения."""
        with self._cv:
            if text is not None:
                self._text = text or ""
            if keyboard is not None:
                self.keyboard = keyboard
            self._done = True
            self._cv.notify_all()
            start = self._thread is None
        if not self.animate:
            # Печать выключена — отправляем сразу целиком, без потока и правок.
            pending = self._text
            if pending.strip():
                self._final(pending)
            return self._message_id
        if start:
            self._start_thread()
        if self._thread is not None:
            self._thread.join(timeout=self.max_sec + 8)
        # Страховка: поток не успел или умер — отправляем/правим текст сами.
        with self._cv:
            pending = self._text
            shown = self._shown
        if not pending:
            return self._message_id
        if self._thread is not None and self._thread.is_alive():
            log.info("печать не успела за %s сек — ставлю текст сразу", self.max_sec)
        if self._message_id is None:
            sent = self.tg.send(self.chat_id, pending, reply_to=self.reply_to,
                                keyboard=self.keyboard)
            if sent:
                self._message_id = sent.get("message_id")
                self._shown = len(pending)
        elif shown < len(pending):
            try:
                self.tg.edit(self.chat_id, self._message_id, pending,
                             keyboard=self.keyboard)
                self._shown = len(pending)
            except Exception as exc:  # noqa: BLE001
                log.debug("печать: финальная правка не удалась: %s", exc)
        return self._message_id

    # ------------------------------------------------------------- рабочий
    def _start_thread(self):
        with self._cv:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="typer", daemon=True)
        self._thread.start()

    def _costs(self, total):
        """Сколько символов дописывать за один шаг."""
        if total <= 0:
            return 1
        target = max(1.0, min(float(self.max_sec), total / max(1.0, float(self.cps))))
        step = int(total * self.interval / target) + 1
        return max(1, min(step, total))

    def _run(self):
        try:
            while True:
                with self._cv:
                    text = self._text
                    done = self._done
                if not text and not done:
                    with self._cv:
                        self._cv.wait(0.25)
                    continue
                if text:
                    self._step(text, done)
                if done and (not self.animate or self._shown >= len(text) or not text):
                    self._final(text)
                    return
                with self._cv:
                    self._cv.wait(self.interval)
        except Exception as exc:  # noqa: BLE001
            log.warning("печать упала: %s", exc)
            self._failed = True

    def _step(self, text, done):
        # короткий ответ — без анимации, отправляем сразу целиком
        if not self.animate or len(text) > config.TYPE_MAX_CHARS:
            return
        now = time.time()
        if self._message_id is None:
            if len(text) < self.min_chars and not done:
                return
            self._shown = min(len(text), max(self.min_chars, self._costs(len(text))))
            sent = self.tg.send(self.chat_id, text[:self._shown],
                                reply_to=self.reply_to)
            if sent:
                self._message_id = sent.get("message_id")
                self._started = time.time()
                self._last_edit = now
            else:
                self._shown = 0
            return
        if self._shown >= len(text):
            return
        # не чаще одного раза в interval: дельты модели нас будят чаще
        if now - self._last_edit < self.interval:
            return
        if done and now - self._started > self.max_sec:
            self._shown = len(text)          # не заставляем ждать: догоняем
        else:
            self._shown = min(len(text), self._shown + self._costs(len(text)))
        self._last_edit = now
        try:
            self.tg.edit(self.chat_id, self._message_id, text[:self._shown])
        except Exception as exc:  # noqa: BLE001
            log.debug("печать: правка не удалась: %s", exc)

    def _final(self, text):
        if self._message_id is None:
            sent = self.tg.send(self.chat_id, text, reply_to=self.reply_to,
                                keyboard=self.keyboard)
            if sent:
                self._message_id = sent.get("message_id")
        else:
            try:
                self.tg.edit(self.chat_id, self._message_id, text,
                             keyboard=self.keyboard)
            except Exception as exc:  # noqa: BLE001
                if "not modified" not in str(exc).lower():
                    log.debug("печать: финальная правка не удалась: %s", exc)
        self._shown = len(text)


def smooth_send(tg, chat_id, text, keyboard=None, reply_to=None, group=False,
                animate=None):
    """Отправить готовый текст с плавной печатью (если включена)."""
    text = text or ""
    if not text.strip():
        return None
    enabled = config.TYPE_ANIMATION if animate is None else bool(animate)
    if not enabled or len(text) > config.TYPE_MAX_CHARS:
        return tg.send(chat_id, text, keyboard=keyboard, reply_to=reply_to)
    if len(text) < config.TYPE_MIN_CHARS:
        return tg.send(chat_id, text, keyboard=keyboard, reply_to=reply_to)
    tw = Typewriter(tg, chat_id, reply_to=reply_to, keyboard=keyboard, group=group,
                    animate=enabled)
    tw.feed(text)
    return tw.finish(text)
