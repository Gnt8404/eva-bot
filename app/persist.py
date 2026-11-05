"""Хранение списка админов вне временной базы облака.

Облачный Render при каждом перезапуске теряет SQLite (/data не постоянный),
поэтому список админов дублируем в файл на диске игрового сервера —
он постоянный и доступен боту через API панели хостинга.
"""
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import rcon

log = logging.getLogger("persist")

ADMINS_FILE = "/bot-data/admins.json"


def _conf(store):
    conf = rcon.panel_cfg(store)
    if not (conf["url"] and conf["key"] and conf["server"] and conf["on"]):
        return None
    return conf


def load_admins(store, attempts=2, timeout=6):
    """Прочитать список админов с диска сервера. True — если прочитали."""
    conf = _conf(store)
    if not conf:
        return False
    if store.get("admins") is not None:
        return True   # список уже есть в базе — файл не должен его затирать
    last_error = None
    for attempt in range(max(1, attempts)):
        try:
            return _load_admins_once(store, conf, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # файла ещё нет — это нормально при первом запуске
                log.info("файл админов на сервере ещё не создан")
                return False
            last_error = exc
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(0.6)
    log.info("админы с сервера не загружены: %s", last_error)
    return False


def _load_admins_once(store, conf, timeout=6):
    url = "%s/api/client/servers/%s/files/download?file=%s" % (
        conf["url"], conf["server"], urllib.parse.quote(ADMINS_FILE, safe=""))
    req = urllib.request.Request(url, headers=rcon._panel_headers(conf))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    url = ((data.get("attributes") or {}).get("url") or "").strip()
    if not url:
        return False
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    items = json.loads(raw)
    if isinstance(items, dict):
        items = items.get("admins") or []
    items = [str(x).strip() for x in items if str(x).strip()]
    if items:
        store.set("admins", ",".join(items))
        log.info("админы загружены из файла сервера (%d)", len(items))
    return True

AUTOPOST_FILE = "/bot-data/autopost.json"
AUTOPOST_KEYS = ["autopost_channels", "autopost_text", "autopost_on",
                 "autoreply_on", "autoreply_rules", "autoreply_default"]


def load_autopost(store):
    """Восстановить настройки постов/автоответов с диска сервера."""
    conf = _conf(store)
    if not conf:
        return False
    try:
        url = "%s/api/client/servers/%s/files/download?file=%s" % (
            conf["url"], conf["server"], urllib.parse.quote(AUTOPOST_FILE, safe=""))
        req = urllib.request.Request(url, headers=rcon._panel_headers(conf))
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        signed = ((data.get("attributes") or {}).get("url") or "").strip()
        if not signed:
            return False
        with urllib.request.urlopen(signed, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in AUTOPOST_KEYS and value:
                    store.set(key, value if isinstance(value, str) else json.dumps(
                        value, ensure_ascii=False))
            log.info("настройки постов/автоответов загружены с сервера")
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        log.info("autopost с сервера не загружен: %s", exc)
        return False
    except Exception as exc:  # noqa: BLE001
        log.info("autopost с сервера не загружен: %s", exc)
        return False


def save_autopost(store):
    conf = _conf(store)
    if not conf:
        return False
    payload = {key: store.get(key) for key in AUTOPOST_KEYS if store.get(key)}
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = "%s/api/client/servers/%s/files/write?file=%s" % (
            conf["url"], conf["server"], urllib.parse.quote(AUTOPOST_FILE, safe=""))
        headers = dict(rcon._panel_headers(conf))
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        log.info("настройки постов/автоответов сохранены на сервере")
        return True
    except Exception as exc:  # noqa: BLE001
        log.info("autopost не сохранился на сервере: %s", exc)
        return False


def save_autopost_async(store):
    threading.Thread(target=save_autopost, args=(store,), daemon=True,
                     name="save-autopost").start()


def load_admins_async(store):
    """Загрузка в фоне: панель может отвечать медленно, старт бота не тормозим."""
    def work():
        try:
            load_admins(store)
        except Exception as exc:  # noqa: BLE001
            log.info("админы: %s", exc)
    threading.Thread(target=work, daemon=True, name="load-admins").start()


def read_text(store, path, timeout=8):
    """Прочитать текстовый файл с сервера (например, banned-players.json)."""
    conf = _conf(store)
    if not conf:
        return None
    try:
        url = "%s/api/client/servers/%s/files/download?file=%s" % (
            conf["url"], conf["server"], urllib.parse.quote(path, safe=""))
        req = urllib.request.Request(url, headers=rcon._panel_headers(conf))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        signed = ((data.get("attributes") or {}).get("url") or "").strip()
        if not signed:
            return None
        with urllib.request.urlopen(signed, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        log.info("не смог прочитать %s: %s", path, exc)
        return None


def save_admins(store):
    """Сохранить список админов на диск сервера. True — если записали."""
    conf = _conf(store)
    if not conf:
        return False
    try:
        payload = json.dumps({"admins": store.admins()}, ensure_ascii=False).encode("utf-8")
        url = "%s/api/client/servers/%s/files/write?file=%s" % (
            conf["url"], conf["server"], urllib.parse.quote(ADMINS_FILE, safe=""))
        headers = dict(rcon._panel_headers(conf))
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        log.info("список админов сохранён на сервере")
        return True
    except Exception as exc:  # noqa: BLE001
        log.info("не смог сохранить админов на сервере: %s", exc)
        return False


def save_admins_async(store):
    threading.Thread(target=save_admins, args=(store,), daemon=True,
                     name="save-admins").start()
