"""Изучение сервера Minecraft по адресу: Server List Ping + Query (GameSpy4) + SRV."""
import json
import logging
import re
import socket
import struct
import time

log = logging.getLogger("mc")


def _varint(value):
    out = b""
    value &= 0xFFFFFFFF
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out += struct.pack("B", b | 0x80)
        else:
            out += struct.pack("B", b)
            return out


def _read_varint(sock):
    num = 0
    for i in range(5):
        chunk = sock.recv(1)
        if not chunk:
            raise IOError("соединение закрыто")
        b = chunk[0]
        num |= (b & 0x7F) << (7 * i)
        if not (b & 0x80):
            return num
    raise IOError("слишком длинный varint")


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise IOError("соединение закрыто")
        buf += chunk
    return buf


def split_addr(addr, default_port=25565):
    addr = (addr or "").strip()
    if addr.startswith("["):
        host, _, rest = addr[1:].partition("]")
        port = rest.lstrip(":")
        return host, int(port) if port.isdigit() else default_port
    if addr.count(":") == 1:
        host, _, port = addr.partition(":")
        if port.isdigit():
            return host, int(port)
    return addr, default_port


def srv_lookup(host):
    """Достаёт реальный host:port из SRV-записи _minecraft._tcp, если есть dnspython."""
    try:
        import dns.resolver
    except Exception:
        return None
    try:
        answers = dns.resolver.resolve("_minecraft._tcp.%s" % host, "SRV")
        rec = sorted(answers, key=lambda r: (r.priority, -r.weight))[0]
        return str(rec.target).rstrip("."), int(rec.port)
    except Exception:
        return None


def ping(host, port=25565, timeout=6):
    """Server List Ping. Возвращает dict или бросает исключение."""
    started = time.time()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        payload = _varint(0)
        payload += _varint(-1 & 0xFFFFFFFF)
        host_b = host.encode("utf-8")
        payload += _varint(len(host_b)) + host_b
        payload += struct.pack(">H", port)
        payload += _varint(1)
        sock.sendall(_varint(len(payload)) + payload)
        sock.sendall(_varint(1) + _varint(0))

        _read_varint(sock)
        _read_varint(sock)
        length = _read_varint(sock)
        raw = _recv_exact(sock, length).decode("utf-8", "replace")
    data = json.loads(raw)
    latency = int((time.time() - started) * 1000)
    players = data.get("players") or {}
    version = data.get("version") or {}
    sample = [p.get("name") for p in (players.get("sample") or []) if isinstance(p, dict)]
    return {
        "online": True,
        "latency": latency,
        "motd": _flatten(data.get("description")),
        "version": version.get("name", "?"),
        "protocol": version.get("protocol"),
        "players_online": players.get("online", 0),
        "players_max": players.get("max", 0),
        "sample": [s for s in sample if s][:20],
        "favicon": bool(data.get("favicon")),
        "mods": _mods(data),
    }


def _mods(data):
    modinfo = data.get("modinfo") or {}
    names = [m.get("modid") for m in (modinfo.get("modList") or []) if isinstance(m, dict)]
    return [n for n in names if n][:40]


def _flatten(desc):
    if desc is None:
        return ""
    if isinstance(desc, str):
        return _strip_codes(desc)
    out = []
    if isinstance(desc, dict):
        if desc.get("text"):
            out.append(str(desc["text"]))
        for extra in desc.get("extra") or []:
            out.append(_flatten(extra))
        if desc.get("translate") and not out:
            out.append(str(desc["translate"]))
    return _strip_codes("".join(out))


def _strip_codes(text):
    import re
    text = re.sub(r"§.", "", text or "")
    text = text.replace("\n", " ").strip()
    return " ".join(text.split())


def query(host, port=25565, timeout=5):
    """GameSpy4 Query — даёт список плагинов, карту, версию софта."""
    session = 0x12345678
    payload = b"\xFE\xFD\x00" + struct.pack(">i", session) + b"\x00\x00\x00\x00"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(payload, (host, port))
            data, _ = sock.recvfrom(65535)
        except Exception:
            sock.sendto(payload, (host, port))
            data, _ = sock.recvfrom(65535)
    if len(data) < 5:
        raise IOError("короткий ответ query")
    parts = [p.decode("utf-8", "replace") for p in data[5:].split(b"\x00") if p != b""]
    info = {}
    for i in range(0, len(parts) - 1, 2):
        info[parts[i].lower()] = parts[i + 1]
    plugins = [p.strip() for p in (info.get("plugins") or "").split(";") if p.strip()]
    return {
        "online": True,
        "software": info.get("version", ""),
        "plugins": plugins[:60],
        "map": info.get("map", ""),
        "gametype": info.get("gametype", ""),
        "query_motd": info.get("hostname", ""),
        "query_players": info.get("players", ""),
    }


def snapshot(addr, timeout=6):
    """Полный слепок сервера: пинг + query + попытка SRV."""
    host, port = split_addr(addr or "")
    if not host:
        return {"online": False, "error": "адрес сервера не задан", "addr": ""}
    result = {"addr": "%s:%d" % (host, port), "host": host, "port": port,
              "checked_at": int(time.time())}
    if port == 25565:
        srv = srv_lookup(host)
        if srv:
            host, port = srv
            result["addr"] = "%s:%d (SRV)" % (host, port)
    try:
        result.update(ping(host, port, timeout))
    except Exception as exc:
        result["online"] = False
        result["error"] = _human_error(exc)
        return result
    try:
        result.update(query(host, port, timeout))
    except Exception:
        pass
    result.setdefault("players_online", 0)
    return result


def _human_error(exc):
    text = str(exc)
    low = text.lower()
    if "timed out" in low or isinstance(exc, socket.timeout):
        return "сервер не отвечает (таймаут)"
    if "refused" in low:
        return "порт закрыт (соединение отклонено)"
    if "name or service not known" in low or "getaddrinfo" in low or "nodename" in low:
        return "адрес не найден (проверь домен/IP)"
    if "unreachable" in low:
        return "адрес недоступен"
    return text[:200]


def split_addrs(text):
    """Разбирает список адресов через запятую/точку с запятой/перенос строки."""
    out = []
    for chunk in re.split(r"[,\n;]+", text or ""):
        chunk = chunk.strip()
        if chunk:
            host, port = split_addr(chunk)
            if host:
                out.append("%s:%d" % (host, port))
    return out


def best_snapshot(addrs, timeout=6):
    """Проверяет адреса по очереди, возвращает первый живой (или последний ответ)."""
    addrs = addrs if isinstance(addrs, (list, tuple)) else split_addrs(addrs)
    last = None
    for addr in addrs:
        snap = snapshot(addr, timeout=timeout)
        if snap.get("online"):
            snap["addr_list"] = list(addrs)
            return snap
        last = snap
    if last:
        last["addr_list"] = list(addrs)
    return last


def bedrock_ping(host, port=19132, timeout=5):
    """RakNet Unconnected Ping — статус Bedrock-сервера."""
    magic = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")
    guid = int(time.time() * 1000) & 0x7FFFFFFFFFFFFFFF
    packet = b"\x01" + struct.pack(">q", int(time.time() * 1000)) + magic + struct.pack(">q", guid)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(packet, (host, port))
        data, _ = sock.recvfrom(65535)
    if not data or data[0] != 0x1C:
        raise IOError("неверный ответ Bedrock")
    # 0x1C: time(8) id(8) magic(16) len(2) payload
    payload = data[35:]
    if len(payload) < 2:
        raise IOError("короткий ответ Bedrock")
    length = struct.unpack(">H", payload[:2])[0]
    text = payload[2:2 + length].decode("utf-8", "replace")
    parts = text.split(";")
    if len(parts) < 6:
        raise IOError("не разобрал ответ Bedrock")
    return {
        "online": True,
        "motd": parts[1],
        "protocol": parts[2],
        "version": parts[3],
        "players_online": int(parts[4] or 0),
        "players_max": int(parts[5] or 0),
        "software": parts[7] if len(parts) > 7 else "",
        "gamemode": parts[8] if len(parts) > 8 else "",
    }


def bedrock_snapshot(addr, timeout=5):
    host, port = split_addr(addr or "", 19132)
    if not host:
        return None
    base = {"addr": "%s:%d" % (host, port), "checked_at": int(time.time())}
    try:
        base.update(bedrock_ping(host, port, timeout))
        return base
    except Exception as exc:
        base["online"] = False
        base["error"] = _human_error(exc)
        return base


def status_text(snap):
    """Готовый текстовый отчёт — только факты, без эмодзи."""
    if not snap or not snap.get("addr"):
        return "Адрес сервера не задан."
    lines = []
    if not snap.get("online"):
        lines.append("Сервер: OFFLINE")
        lines.append("Адрес: %s" % snap["addr"])
        lines.append("Причина: %s" % snap.get("error", "нет ответа"))
        return "\n".join(lines)
    lines.append("Сервер: ONLINE")
    lines.append("Адрес: %s" % snap["addr"])
    lines.append("Версия: %s" % snap.get("version", "?"))
    lines.append("Игроков: %s/%s" % (snap.get("players_online", 0), snap.get("players_max", 0)))
    lines.append("Пинг: %s ms" % snap.get("latency", "?"))
    if snap.get("motd"):
        lines.append("MOTD: %s" % snap["motd"])
    if snap.get("software"):
        lines.append("Софт: %s" % snap["software"])
    if snap.get("map"):
        lines.append("Карта: %s" % snap["map"])
    if snap.get("plugins"):
        lines.append("Плагины: %s" % ", ".join(snap["plugins"][:25]))
    if snap.get("mods"):
        lines.append("Моды: %s" % ", ".join(snap["mods"][:15]))
    if snap.get("sample"):
        lines.append("Сейчас играют: %s" % ", ".join(snap["sample"]))
    return "\n".join(lines)


def bedrock_text(snap):
    if not snap:
        return ""
    if not snap.get("online"):
        return "Bedrock: нет ответа (%s)" % snap.get("error", "нет данных")
    lines = ["Bedrock: ONLINE",
             "Адрес: %s" % snap.get("addr"),
             "Версия: %s" % snap.get("version", "?"),
             "Игроков: %s/%s" % (snap.get("players_online", 0), snap.get("players_max", 0))]
    if snap.get("motd"):
        lines.append("MOTD: %s" % snap["motd"])
    if snap.get("software"):
        lines.append("Софт: %s" % snap["software"])
    return "\n".join(lines)


def full_status_text(snap, bedrock=None):
    """Отчёт по Java + Bedrock одним сообщением."""
    java = status_text(snap)
    if not bedrock:
        return java
    return java + "\n\n" + bedrock_text(bedrock)
