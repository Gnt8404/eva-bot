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

ADMINS_FILE = "/plugins/Eva/bot-admins.json"


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

def load_admins_async(store):
    """Загрузка в фоне: панель может отвечать медленно, старт бота не тормозим."""
    def work():
        try:
            load_admins(store)
        except Exception as exc:  # noqa: BLE001
            log.info("админы: %s", exc)
    threading.Thread(target=work, daemon=True, name="load-admins").start()


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
