"""Хранение списка админов вне временной базы облака.

Облачный Render при каждом перезапуске теряет SQLite (/data не постоянный),
поэтому список админов дублируем в файл на диске игрового сервера —
он постоянный и доступен боту через API панели хостинга.
"""
import json
import logging
import threading
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


def load_admins(store):
    """Прочитать список админов с диска сервера. True — если прочитали."""
    conf = _conf(store)
    if not conf:
        return False
    try:
        data = rcon._panel_api(
            conf, "/files/download?file=" + urllib.parse.quote(ADMINS_FILE, safe=""))
        url = ((data.get("attributes") or {}).get("url") or "").strip()
        if not url:
            return False
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = resp.read().decode("utf-8", "replace")
        items = json.loads(raw)
        if isinstance(items, dict):
            items = items.get("admins") or []
        items = [str(x).strip() for x in items if str(x).strip()]
        if items:
            store.set("admins", ",".join(items))
            log.info("админы загружены из файла сервера (%d)", len(items))
        return True
    except Exception as exc:  # noqa: BLE001
        log.info("админы с сервера не загружены: %s", exc)
        return False


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
