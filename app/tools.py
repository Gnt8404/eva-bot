"""Действия Евы: статус, тикеты, перезапуск сервера (через панель)."""
import json
import logging
import time
import urllib.parse
import urllib.request

from . import mc, rcon

log = logging.getLogger("tools")


def status(store):
    addrs = store.server_addrs()
    if not addrs:
        return False, "Адрес сервера не задан."
    snap = mc.best_snapshot(addrs)
    if snap.get("online"):
        return True, ("Сервер онлайн, игроков %s/%s, версия %s."
                      % (snap.get("players_online", "?"), snap.get("players_max", "?"),
                         snap.get("version", "?")))
    return False, "Сервер сейчас не отвечает: %s" % (snap.get("error") or "нет ответа")


def _panel_conf(store):
    conf = rcon.panel_cfg(store)
    if not (conf["url"] and conf["key"] and conf["server"] and conf["on"]):
        return None
    return conf


def server_state(store):
    """running/starting/stopping/offline или ''."""
    conf = _panel_conf(store)
    if not conf:
        return ""
    try:
        data = rcon._panel_api(conf, "/resources")
        return str((data.get("attributes") or {}).get("current_state") or "").lower()
    except Exception:
        return ""


def restart(store):
    """Перезапуск через API панели. Возвращает (ok, текст)."""
    conf = _panel_conf(store)
    if not conf:
        return False, "Канал панели не настроен — перезапуск недоступен."
    url = "%s/api/client/servers/%s/power" % (conf["url"], conf["server"])
    body = json.dumps({"signal": "restart"}).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={**rcon._panel_headers(conf),
                                          "Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        log.info("перезапуск сервера отправлен")
        return True, "Перезапуск отправлен. Сервер вернётся через 30-60 секунд."
    except Exception as exc:  # noqa: BLE001
        return False, "Не удалось перезапустить: %s" % exc


def ticket(store, user, kind="other", subject="", body=""):
    """Создать тикет в базе бота. Возвращает (ok, id)."""
    try:
        uid = (user or {}).get("id")
        uname = (user or {}).get("username") or user.get("first_name") or ""
        now = int(time.time())
        with store.lock:
            cur = store.conn.execute(
                "INSERT INTO tickets(user_id,username,kind,subject,body,status,"
                "created_at,updated_at) VALUES(?,?,?,?,?,'new',?,?)",
                (uid, uname, (kind or "other")[:40], (subject or "")[:200],
                 (body or "")[:3000], now, now))
            store.conn.commit()
            return True, cur.lastrowid
    except Exception as exc:  # noqa: BLE001
        log.info("тикет не создался: %s", exc)
        return False, 0
