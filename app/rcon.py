"""RCON-клиент (Source RCON) — командная консоль сервера Minecraft прямо из Telegram.

Протокол: int32 длина | int32 id | int32 тип | тело + \\0 + \\0
Типы: 3 — авторизация, 2 — команда/ответ, 0 — значение ответа.

Настройки берутся из базы бота:
    rcon_host, rcon_port (по умолчанию 25575), rcon_pass, rcon_on
"""
import logging
import re
import socket
import struct
import time

log = logging.getLogger("rcon")

TYPE_AUTH = 3
TYPE_AUTH_RESPONSE = 2
TYPE_COMMAND = 2
TYPE_RESPONSE = 0

DEFAULT_PORT = 25575

# Если RCON не отвечает (например, хостинг не пускает порт), 10 минут не тратим
# время на попытки подключения — сразу идём в другие каналы консоли.
_RCON_BAD_UNTIL = 0.0


class RconError(Exception):
    pass


class Rcon:
    def __init__(self, host, port=DEFAULT_PORT, password="", timeout=8):
        self.host = (host or "").strip()
        self.port = int(port or DEFAULT_PORT)
        self.password = password or ""
        self.timeout = timeout
        self.sock = None
        self.request_id = 1

    # ---------- низкий уровень ----------
    def _send(self, pkt_id, pkt_type, body):
        payload = struct.pack("<ii", pkt_id, pkt_type) + body.encode("utf-8") + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(payload)) + payload)

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RconError("соединение закрыто сервером")
            buf += chunk
        return buf

    def _read(self):
        head = self._recv_exact(4)
        (length,) = struct.unpack("<i", head)
        if length < 10 or length > 1 << 20:
            raise RconError("битый пакет от сервера")
        data = self._recv_exact(length)
        pkt_id, pkt_type = struct.unpack("<ii", data[:8])
        body = data[8:-2].decode("utf-8", "replace")
        return pkt_id, pkt_type, body

    # ---------- публичное ----------
    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self._send(self.request_id, TYPE_AUTH, self.password)
        pkt_id, pkt_type, _ = self._read()
        if pkt_id == -1:
            raise RconError("неверный пароль RCON")
        if pkt_id != self.request_id:
            raise RconError("сервер не принял авторизацию")
        log.info("RCON подключён к %s:%s", self.host, self.port)
        return True

    def command(self, cmd):
        """Отправляет команду, возвращает текст ответа сервера."""
        if not self.sock:
            raise RconError("нет соединения")
        self.request_id += 1
        rid = self.request_id
        self._send(rid, TYPE_COMMAND, cmd)
        parts = []
        while True:
            try:
                pkt_id, pkt_type, body = self._read()
            except socket.timeout:
                break
            except RconError:
                break
            if pkt_id == rid:
                parts.append(body)
                # сервер может прислать продолжение — ждём ещё чуть-чуть
                self.sock.settimeout(0.4)
                continue
            parts.append(body)
            self.sock.settimeout(0.4)
        self.sock.settimeout(self.timeout)
        return "\n".join(p for p in parts if p).strip()

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# ---------------------------------------------------------------------------
# настройки в базе
# ---------------------------------------------------------------------------
def cfg(store):
    """Текущие настройки RCON: база, затем переменные окружения, затем адрес сервера."""
    from . import config
    host = (store.get("rcon_host") or "").strip()
    if not host:
        host = config.RCON_HOST
    if not host:
        from . import mc
        addr = store.server_addr()
        if addr:
            host = mc.split_addr(addr)[0]
    port_raw = store.get("rcon_port")
    try:
        port = int(port_raw) if port_raw else config.RCON_PORT
    except Exception:
        port = config.RCON_PORT
    password = (store.get("rcon_pass") or "").strip() or config.RCON_PASS
    on_default = "on" if config.RCON_ON else "off"
    return {
        "host": host,
        "port": port,
        "pass": password,
        "on": (store.flag("rcon_on", on_default) == "on"),
        "template": store.get("wl_template") or "whitelist add {nick}",
        "untemplate": store.get("wl_untemplate") or "whitelist remove {nick}",
    }


def ready(store):
    conf = cfg(store)
    return bool(conf["host"] and conf["pass"])


def run(store, cmd, timeout=8):
    """Выполнить команду на сервере. Возвращает (ok, текст)."""
    global _RCON_BAD_UNTIL
    conf = cfg(store)
    if not conf["host"]:
        return False, ("Не задан адрес RCON. Укажи его так:\n"
                       "/rcon set <адрес> <порт> <пароль>")
    if not conf["pass"]:
        return False, ("Не задан пароль RCON. Укажи так:\n/rcon pass <пароль>")
    if not conf["on"]:
        return False, "RCON выключен. Включить: /rcon on"
    if time.time() < _RCON_BAD_UNTIL:
        return False, "RCON недавно не отвечал — сразу пробую другой канал"
    client = Rcon(conf["host"], conf["port"], conf["pass"], timeout=timeout)
    try:
        client.connect()
        out = client.command(cmd)
        _RCON_BAD_UNTIL = 0.0
        return True, out or "(сервер не вернул текст)"
    except RconError as exc:
        _RCON_BAD_UNTIL = time.time() + 3600
        return False, "Ошибка RCON: %s" % exc
    except (socket.timeout, OSError) as exc:
        _RCON_BAD_UNTIL = time.time() + 3600
        return False, ("Не удалось подключиться к %s:%s (%s). "
                       "Проверь, что RCON включён в server.properties "
                       "(enable-rcon=true) и открыт порт." % (conf["host"], conf["port"], exc))
    finally:
        client.close()


def test(store, timeout=8):
    ok, out = run(store, "list", timeout=timeout)
    if ok:
        return True, out
    return False, out


# ---------------------------------------------------------------------------
# дополнительные каналы консоли: агент в JVM, панель хостинга, шелл хоста
# ---------------------------------------------------------------------------
# Порядок попыток: RCON -> агент (javaagent) -> панель -> шелл (/proc/<pid>/fd/0).
# Агент speaks на трёх транспортах: TCP-сокет, файловый канал (console.cmd /
# console.out) и HTTP (альтернативная сборка). Любой из них включается отдельно,
# остальные просто пропускаются.

import json
import subprocess
import urllib.error
import urllib.request

AGENT_PORT = 25576
AGENT_END = "--END--"
AGENT_GREETING = "console-agent ready"


def agent_cfg(store):
    try:
        port = int(store.get("agent_port") or AGENT_PORT)
    except (TypeError, ValueError):
        port = AGENT_PORT
    return {
        "host": (store.get("agent_host") or "127.0.0.1").strip(),
        "port": port,
        "token": (store.get("agent_token") or "").strip(),
        "url": (store.get("agent_url") or "").strip().rstrip("/"),
        "dir": (store.get("agent_dir") or "").strip(),
        "on": store.flag("agent_on", "on") == "on",
    }


def panel_cfg(store):
    from . import config
    return {
        "url": ((store.get("panel_url") or "").strip().rstrip("/")
                or config.PANEL_URL),
        "key": (store.get("panel_key") or "").strip() or config.PANEL_KEY,
        "server": ((store.get("panel_server") or "").strip()
                   or (store.get("panel_server_id") or "").strip()
                   or config.PANEL_SERVER),
        "on": store.flag("panel_on", "on" if config.PANEL_ON else "off") == "on",
    }


def shell_on(store):
    return store.flag("shell_console", "off") == "on"


def _strip_slash(cmd):
    text = (cmd or "").strip()
    while text.startswith("/"):
        text = text[1:]
    return text


# --------------------------------------------------------------- TCP-канал
def agent_socket_run(store, cmd, timeout=10):
    """Агент по TCP: токен -> приветствие -> команда -> строки до --END--."""
    conf = agent_cfg(store)
    text = _strip_slash(cmd)
    if not text:
        return False, "пустая команда"
    try:
        sock = socket.create_connection((conf["host"], conf["port"]), timeout)
    except OSError as exc:
        return False, "агент не отвечает (%s:%s): %s" % (conf["host"], conf["port"], exc)
    try:
        sock.settimeout(timeout)
        stream = sock.makefile("rwb")
        if conf["token"]:
            stream.write((conf["token"] + "\n").encode("utf-8"))
            stream.flush()
            hello = stream.readline().decode("utf-8", "replace").strip()
            if hello != "auth ok":
                return False, "агент не принял токен"
        stream.readline()                       # приветствие агента
        stream.write((text + "\n").encode("utf-8"))
        stream.flush()
        lines = []
        while True:
            raw = stream.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line == AGENT_END:
                break
            lines.append(line)
        out = "\n".join(lines).strip()
        return True, (out or "(выполнено)")
    except OSError as exc:
        return False, "обрыв канала агента: %s" % exc
    finally:
        try:
            sock.close()
        except OSError:
            pass


# ----------------------------------------------------------- файловый канал
def agent_file_run(store, cmd, timeout=10):
    """Агент через файлы: пишем в console.cmd, читаем ответ из console.out."""
    conf = agent_cfg(store)
    text = _strip_slash(cmd)
    if not conf["dir"] or not text:
        return None
    import os
    import time as _time
    cmd_file = os.path.join(conf["dir"], "console.cmd")
    out_file = os.path.join(conf["dir"], "console.out")
    try:
        before = os.path.getsize(out_file) if os.path.exists(out_file) else 0
        with open(cmd_file, "a", encoding="utf-8") as handle:
            handle.write(("\n" + text + "\n"))
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            _time.sleep(0.4)
            if not os.path.exists(out_file):
                continue
            with open(out_file, "r", encoding="utf-8", errors="replace") as handle:
                data = handle.read()
            if len(data) <= before:
                continue
            fresh = data[before:]
            if ">>> %s" % text in fresh and fresh.rstrip().endswith("---"):
                return True, fresh.strip()[-1500:]
    except OSError as exc:
        return False, "файловый канал агента: %s" % exc
    return False, "агент не ответил в console.out за %d сек" % timeout


# --------------------------------------------------------------- HTTP-канал
def _post_json(url, payload, headers=None, timeout=10):
    body = json.dumps(payload).encode("utf-8")
    head = {"Content-Type": "application/json"}
    if headers:
        head.update(headers)
    req = urllib.request.Request(url, data=body, headers=head, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError:
        return {"raw": raw}


def agent_http_run(store, cmd, timeout=10):
    conf = agent_cfg(store)
    if not conf["url"]:
        return None
    try:
        data = _post_json(conf["url"] + "/command",
                          {"token": conf["token"], "command": _strip_slash(cmd)},
                          timeout=timeout)
    except (urllib.error.URLError, OSError) as exc:
        return False, "агент недоступен: %s" % exc
    if isinstance(data, dict) and data.get("output") is not None:
        return bool(data.get("ok", True)), str(data["output"])
    if isinstance(data, dict) and data.get("raw"):
        return True, str(data["raw"])
    return False, "агент вернул неожиданный ответ: %s" % str(data)[:200]


def agent_run(store, cmd, timeout=10):
    """Любой транспорт агента. None — если ничего не настроено."""
    conf = agent_cfg(store)
    if not conf["on"]:
        return None
    if conf["url"]:
        return agent_http_run(store, cmd, timeout)
    if conf["host"]:
        return agent_socket_run(store, cmd, timeout)
    if conf["dir"]:
        return agent_file_run(store, cmd, timeout)
    return None


# ------------------------------------------------------------- панель хостинга
PANEL_API_TIMEOUT = 15
LOG_PREFIX = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s*\[[^\]]+\]:\s?")
LOG_COLOR = re.compile(r"[§&][0-9a-fk-orx]", re.IGNORECASE)


def _panel_headers(conf):
    return {"Authorization": "Bearer " + conf["key"],
            "Accept": "application/json",
            "User-Agent": "MC-Eva-Bot/1.0"}


def _panel_api(conf, path):
    url = "%s/api/client/servers/%s%s" % (conf["url"], conf["server"], path)
    req = urllib.request.Request(url, headers=_panel_headers(conf))
    with urllib.request.urlopen(req, timeout=PANEL_API_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _panel_log_size(conf):
    """Размер logs/latest.log в байтах (None — файла нет)."""
    data = _panel_api(conf, "/files/list?directory=%2Flogs")
    for item in data.get("data") or []:
        attrs = (item or {}).get("attributes") or {}
        if attrs.get("name") == "latest.log":
            try:
                return int(attrs.get("size") or 0)
            except (TypeError, ValueError):
                return 0
    return None


def _panel_read_log_from(conf, offset):
    """Прочитать latest.log начиная с байта offset (Range, с запасным путём)."""
    data = _panel_api(conf, "/files/download?file=%2Flogs%2Flatest.log")
    url = ((data.get("attributes") or {}).get("url") or "").strip()
    if not url:
        return ""
    req = urllib.request.Request(
        url, headers={"Range": "bytes=%d-" % max(0, offset)})
    try:
        with urllib.request.urlopen(req, timeout=PANEL_API_TIMEOUT) as resp:
            raw = resp.read()
            code = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        if exc.code == 416:
            return ""
        with urllib.request.urlopen(url, timeout=PANEL_API_TIMEOUT) as resp:
            raw = resp.read()
            code = 200
    except (urllib.error.URLError, OSError):
        return ""
    if code == 200 and offset and len(raw) > offset:
        raw = raw[offset:]          # сервер отдал файл целиком — отрезаем по байтам
    elif code == 200 and offset:
        return ""
    return raw.decode("utf-8", "replace")


def _clean_log_lines(text, limit=2500):
    lines = []
    for line in (text or "").splitlines():
        line = LOG_COLOR.sub("", line).strip()
        if not line:
            continue
        line = LOG_PREFIX.sub("", line)
        lines.append(line)
    return "\n".join(lines).strip()[:limit]


def panel_run(store, cmd, timeout=12):
    """Команда через client API панели (Pterodactyl) + вывод из консоли.

    Панель принимает команду, но ответ не возвращает: перед отправкой
    запоминаем размер logs/latest.log, после — дочитываем только новые строки
    (это и есть ответ сервера на команду).
    """
    conf = panel_cfg(store)
    if not (conf["url"] and conf["key"] and conf["server"] and conf["on"]):
        return None
    try:
        before = _panel_log_size(conf)
    except Exception:
        before = None
    url = "%s/api/client/servers/%s/command" % (conf["url"], conf["server"])
    try:
        _post_json(url, {"command": _strip_slash(cmd)},
                   headers=_panel_headers(conf), timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False, "панель: ключу не хватает прав на консоль (HTTP %s)" % exc.code
        return False, "панель: HTTP %s" % exc.code
    except (urllib.error.URLError, OSError) as exc:
        return False, "панель недоступна: %s" % exc
    deadline = time.time() + 4.0
    text = ""
    # после команды обычно достаточно 0.8-2 сек: сначала ждём, потом читаем
    time.sleep(1.0)
    for attempt in range(3):
        try:
            now = _panel_log_size(conf)
        except Exception:
            now = None
        if before is not None and now is not None and now > before:
            text = _panel_read_log_from(conf, before)
            if text.strip():
                break
        elif before is None and now:
            text = _panel_read_log_from(conf, max(0, now - 6000))
            break
        time.sleep(0.7)
    if not text.strip():
        try:
            now = _panel_log_size(conf) or 0
            text = _panel_read_log_from(conf, max(0, now - 4000))
        except Exception:
            text = ""
    clean = _clean_log_lines(text)
    if not clean:
        clean = "команда отправлена панелью, вывода в консоли нет"
    return True, clean


# ------------------------------------------------------------------ шелл хоста
def shell_run(store, cmd, timeout=10):
    """Прямая запись в stdio процесса сервера (бот и сервер на одной машине)."""
    if not shell_on(store):
        return None
    pattern = store.get("shell_pattern") or "unix_args.txt"
    try:
        pids = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True,
                              timeout=timeout).stdout.split()
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "не смог найти процесс сервера: %s" % exc
    if not pids:
        return False, "процесс сервера с шаблоном «%s» не найден" % pattern
    pid = pids[0]
    try:
        with open("/proc/%s/fd/0" % pid, "w") as handle:
            handle.write(cmd + "\n")
        return True, "команда отправлена в stdin сервера (pid %s), ответ в latest.log" % pid
    except OSError as exc:
        return False, "не смог записать в stdin сервера: %s" % exc


def run_any(store, cmd, timeout=8):
    """Выполнить команду по первому живому каналу. Возвращает (ok, текст, канал)."""
    ok, out = run(store, cmd, timeout=timeout)
    if ok:
        return True, out, "RCON"
    rcon_error = out
    for name, fn in (("агент", agent_run), ("панель", panel_run), ("шелл", shell_run)):
        try:
            result = fn(store, cmd, timeout=timeout)
        except Exception as exc:                      # noqa: BLE001
            result = False, "%s: %s" % (name, exc)
        if result is None:
            continue
        if result[0]:
            return True, result[1], name
        log.info("канал %s не сработал: %s", name, result[1])
    return False, rcon_error, ""


def channels(store):
    """Что настроено — для админской справки."""
    conf = cfg(store)
    agent = agent_cfg(store)
    panel = panel_cfg(store)
    if agent["url"]:
        agent_info = agent["url"]
    elif agent["host"]:
        agent_info = "%s:%s" % (agent["host"], agent["port"])
    else:
        agent_info = agent["dir"] or "не задан"
    return [
        ("RCON", bool(conf["host"] and conf["pass"] and conf["on"]),
         "%s:%s" % (conf["host"] or "не задан", conf["port"])),
        ("агент в JVM", bool(agent["on"] and (agent["url"] or agent["host"] or agent["dir"])),
         agent_info),
        ("панель хостинга", bool(panel["url"] and panel["key"] and panel["server"] and panel["on"]),
         panel["url"] or "не задан"),
        ("шелл хоста", shell_on(store), store.get("shell_pattern") or "unix_args.txt"),
    ]
