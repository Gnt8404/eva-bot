"""База знаний о сервере: плагины, датапаки, кастомные плагины, механики.

Знания лежат обычными markdown-файлами в папке knowledge/. Их можно править без
кода: бот перечитывает файлы при старте (и по команде /kb reload для админа).

Формат записи описан в knowledge/_FORMAT.md:
    ## Заголовок
    ключи: слово1, слово2, ...
    кратко: одна строка для списка
    ...текст...

Модуль умеет:
  * catalog()  — список записей для команд /plugins и /datapacks;
  * lookup()   — найти записи по вопросу игрока;
  * block()    — готовый текстовый блок для системного промпта ИИ;
  * get()      — найти запись по названию (для /plugins <название>).
"""
import logging
import os
import threading

from . import config

log = logging.getLogger("kb")

DIR = os.path.join(config.ROOT, "knowledge")

_lock = threading.RLock()
_entries = None


class Entry:
    """Одна запись базы знаний."""

    __slots__ = ("title", "keys", "brief", "body", "source", "hidden")

    def __init__(self, title, keys, brief, body, source, hidden=False):
        self.title = title
        self.keys = keys
        self.brief = brief
        self.body = body
        self.source = source
        self.hidden = hidden

    def render(self, limit=3500):
        text = self.body.strip()
        if limit and len(text) > limit:
            text = text[:limit].rstrip() + "…"
        return "%s\n%s" % (self.title, text) if text else self.title

    def __repr__(self):
        return "<Entry %r>" % self.title


def _parse(text, source):
    """Разбирает один файл: записи начинаются со строки "## "."""
    out = []
    cur = None
    for line in (text or "").splitlines():
        if line.startswith("## "):
            if cur is not None:
                out.append(cur)
            cur = {"title": line[3:].strip(), "keys": [], "brief": "", "body": [],
                   "hidden": False}
            continue
        if cur is None:
            continue
        stripped = line.strip()
        low = stripped.lower()
        if not cur["keys"] and (low.startswith("ключи:") or low.startswith("keys:")):
            raw = stripped.split(":", 1)[1] if ":" in stripped else ""
            cur["keys"] = [k.strip().lower() for k in raw.split(",") if k.strip()]
            continue
        if not cur["brief"] and low.startswith("кратко:"):
            cur["brief"] = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            continue
        if low.startswith(("видно:", "список:", "публично:")) and not cur["body"]:
            value = stripped.split(":", 1)[1].strip().lower() if ":" in stripped else ""
            if value in ("нет", "no", "off", "hide", "скрыть", "0", "false"):
                cur["hidden"] = True
            continue
        cur["body"].append(line)
    if cur is not None:
        out.append(cur)
    entries = []
    for item in out:
        if not item["title"]:
            continue
        body = "\n".join(item["body"]).strip()
        entries.append(Entry(item["title"], item["keys"], item["brief"], body, source,
                             item["hidden"]))
    return entries


def load(force=False):
    """Читает knowledge/*.md. Возвращает число записей."""
    global _entries
    with _lock:
        if _entries is not None and not force:
            return len(_entries)
        found = []
        try:
            names = sorted(os.listdir(DIR))
        except OSError as exc:
            log.info("папка знаний не найдена (%s) — работаю без неё", exc)
            _entries = []
            return 0
        for name in names:
            if name.startswith("_") or not name.lower().endswith(".md"):
                continue
            path = os.path.join(DIR, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except OSError as exc:
                log.warning("не смог прочитать %s: %s", path, exc)
                continue
            entries = _parse(text, name[:-3])
            if entries:
                found.extend(entries)
                log.info("знания: %s — записей %d", name, len(entries))
            else:
                log.info("знания: %s — пусто или неверный формат", name)
        _entries = found
        log.info("знания о сервере: всего записей %d", len(found))
        return len(found)


def entries(source=None):
    """Все записи (или только из файла source, например "plugins")."""
    load()
    with _lock:
        items = list(_entries or [])
    if source:
        items = [e for e in items if e.source == source]
    return items


def count(source=None):
    return len(entries(source))


def sources():
    load()
    with _lock:
        return sorted({e.source for e in (_entries or [])})


def catalog(source=None, include_hidden=False):
    """[(заголовок, кратко), ...] — для списков.

    По умолчанию скрытые записи (строка «видно: нет») не попадают в список:
    так /plugins и /datapacks показывают только то, чем пользуются игроки.
    """
    items = entries(source)
    if not include_hidden:
        items = [e for e in items if not e.hidden]
    return [(e.title, e.brief) for e in items]


def catalog_all(source=None):
    """Список вместе со скрытыми записями — для админской команды /kb."""
    return catalog(source, include_hidden=True)


def _score(entry, low):
    score = 0
    for key in entry.keys:
        if not key:
            continue
        if key in low:
            score += 3 if " " in key else (2 if len(key) >= 5 else 1)
    return score


def lookup(question, limit=3, min_score=2):
    """Записи, подходящие под вопрос. Сортировка по попаданию ключей."""
    low = (question or "").lower().replace("ё", "е")
    if len(low.strip()) < 2:
        return []
    scored = []
    for entry in entries():
        score = _score(entry, low)
        if score >= min_score:
            scored.append((score, entry))
    if not scored:
        return []
    scored.sort(key=lambda pair: (-pair[0], len(pair[1].title)))
    return [entry for _, entry in scored[:limit]]


def block(question, limit_chars=3200, limit=3):
    """Текст для промпта модели: подходящие записи, коротко."""
    found = lookup(question, limit=limit)
    if not found:
        return ""
    parts = []
    used = 0
    for entry in found:
        piece = entry.render(limit=limit_chars // max(1, limit) + 400)
        if used + len(piece) > limit_chars and parts:
            break
        parts.append(piece)
        used += len(piece)
    return "\n\n".join(parts)[:limit_chars]


def get(query):
    """Найти запись по названию (точно или вхождением). None, если нет."""
    q = (query or "").strip().lower().replace("ё", "е")
    if not q:
        return None
    items = entries()
    for entry in items:
        if entry.title.lower().replace("ё", "е") == q:
            return entry
    for entry in items:
        title = entry.title.lower().replace("ё", "е")
        if q in title or title in q:
            return entry
    for entry in items:
        if any(q == k or (len(q) >= 4 and q in k) for k in entry.keys):
            return entry
    found = lookup(query, limit=1, min_score=2)
    return found[0] if found else None


def reload():
    """Перечитать знания (админская команда)."""
    return load(force=True)


load()
