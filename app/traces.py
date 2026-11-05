"""Трейсы и логи эвалов: JSONL в каталоге данных бота."""
import json
import logging
import os
import threading
import time

from . import config

log = logging.getLogger("traces")
_LOCK = threading.Lock()
MAX_BYTES = 3 * 1024 * 1024


def _path(name):
    return os.path.join(config.DATA_DIR, name)


def _append(name, event):
    if not getattr(config, "TRACES_ON", True):
        return
    event = dict(event or {})
    event.setdefault("ts", round(time.time(), 3))
    line = json.dumps(event, ensure_ascii=False)[:4000] + "\n"
    try:
        path = _path(name)
        with _LOCK:
            if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
                os.replace(path, path + ".1")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception as exc:  # noqa: BLE001
        log.info("трейс не записался: %s", exc)


def trace(event):
    _append("traces.jsonl", event)


def eval_log(event):
    _append("evals.jsonl", event)


def tail(limit=20):
    try:
        with open(_path("traces.jsonl"), "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-limit:]
        return [json.loads(x) for x in lines if x.strip()]
    except Exception:
        return []
