"""RAG по официальному сборнику правил Aetherlyn.

Без внешних зависимостей: лёгкий BM25 по чанкам из knowledge/aetherlyn_rag.json.
Если задан QDRANT_URL — сначала пробуем векторный поиск в Qdrant (REST), а при
недоступности откатываемся на BM25. Векторы — детерминированный hashing, чтобы
не тащить модель эмбеддингов в рантайм.
"""
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

log = logging.getLogger("rag")

_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "knowledge", "aetherlyn_rag.json")

_STOP = set("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если
уже или ни быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей
может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего
раз тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь
этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при
наконец два об другой хоть после над больше тот через эти нас про всего них какая
много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя
такой им более всегда конечно всю между
the and with this that please hello you your for are was were from have has
""".split())

_SUFFIX = sorted(["иями", "ями", "ами", "ыми", "ими", "ениями", "ения", "ению",
                   "ение", "ации", "ация", "ацию", "овать", "евать", "ать", "ять",
                   "ить", "еть", "уть", "ила", "или", "ило", "ыла", "ла", "ли",
                   "ло", "ут", "ют", "ат", "ят", "ся", "сь", "ий", "ия", "ие",
                   "ый", "ая", "ое", "ые", "ую", "юю", "ом", "ем", "ой", "ей",
                   "ам", "ям", "ах", "ях", "ов", "ев", "иями", "ьми", "ми",
                   "ы", "и", "а", "я", "е", "о", "у", "ю", "ь"], key=len, reverse=True)

SYNONYMS = {
    "грифер": ["разруш", "постройк", "ущерб", "нарушен", "санкц", "правил"],
    "грифир": ["разруш", "постройк", "ущерб", "нарушен", "санкц"],
    "забан": ["бан", "блокиров", "санкц", "модерац", "обжал"],
    "разбан": ["бан", "обжал", "апелляц", "жалоб", "санкц"],
    "несправедлив": ["обжал", "жалоб", "модерац", "санкц"],
    "апелляц": ["обжал", "жалоб", "санкц", "модерац"],
    "проверк": ["сборк", "мод", "ресурс", "датапак", "файл"],
    "сборк": ["проверк", "мод", "ресурс", "датапак", "файл"],
    "конфиденц": ["персональн", "данн", "защит", "соглас"],
    "кража": ["нарушен", "санкц", "правил", "ущерб"],
}


def _norm(word):
    w = word.lower().replace("ё", "е")
    for _ in range(2):
        for suf in _SUFFIX:
            if len(w) - len(suf) >= 3 and w.endswith(suf):
                w = w[:-len(suf)]
                break
    return w


def _expand(token):
    out = [token]
    for key, extra in SYNONYMS.items():
        if token.startswith(key) or key.startswith(token):
            out.extend(extra)
    return out


def tokens(text, expand=False):
    words = re.findall(r"[a-zа-я0-9_]{2,}", (text or "").lower().replace("ё", "е"))
    out = []
    for w in words:
        if w in _STOP or len(w) < 3:
            continue
        norm = _norm(w)
        if expand:
            out.extend(_expand(norm))
        else:
            out.append(norm)
    return out


def hash_vector(text, dim=256):
    """Простой детерминированный вектор для Qdrant (bag of hashed tokens)."""
    vec = [0.0] * dim
    for tok in tokens(text):
        h = 2166136261
        for ch in tok:
            h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
        vec[h % dim] += 1.0
        vec[(h >> 8) % dim] += 0.5
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


class _Index:
    def __init__(self, path=_JSON):
        self.path = path
        self.chunks = []
        self.df = {}
        self.tf = []
        self.lengths = []
        self.avg = 0.0
        self.loaded_at = 0.0

    def load(self):
        try:
            stat = os.stat(self.path)
        except OSError:
            self.chunks = []
            return False
        if self.chunks and stat.st_mtime <= self.loaded_at:
            return True
        with open(self.path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.chunks = [c for c in (data.get("chunks") or [])
                       if isinstance(c, dict) and c.get("text")]
        self.tf = []
        self.lengths = []
        self.df = {}
        for chunk in self.chunks:
            toks = tokens(chunk.get("section", "") + " " + chunk["text"])
            counts = {}
            for t in toks:
                counts[t] = counts.get(t, 0) + 1
            self.tf.append(counts)
            self.lengths.append(max(1, len(toks)))
            for t in counts:
                self.df[t] = self.df.get(t, 0) + 1
        self.avg = sum(self.lengths) / float(max(1, len(self.lengths)))
        self.loaded_at = stat.st_mtime
        return bool(self.chunks)

    def bm25(self, query, k=4, expand=True):
        if not self.load():
            return []
        q = tokens(query, expand=expand)
        if not q:
            return []
        n = len(self.chunks)
        scores = []
        k1, b = 1.5, 0.75
        for idx, counts in enumerate(self.tf):
            score = 0.0
            dl = self.lengths[idx]
            for term in q:
                freq = counts.get(term)
                if not freq:
                    continue
                idf = math.log(1.0 + (n - self.df.get(term, 0) + 0.5)
                               / (self.df.get(term, 0) + 0.5))
                score += idf * (freq * (k1 + 1)) / (
                    freq + k1 * (1 - b + b * dl / self.avg))
            if score:
                scores.append((score, idx))
        scores.sort(reverse=True)
        out = []
        for score, idx in scores[:k]:
            chunk = self.chunks[idx]
            out.append({"id": chunk.get("id"), "section": chunk.get("section"),
                        "text": chunk["text"], "score": round(score, 3),
                        "source": "bm25"})
        return out

    def get(self, chunk_id):
        for chunk in self.chunks:
            if chunk.get("id") == chunk_id:
                return chunk
        return None


INDEX = _Index()


def search(query, k=4):
    """Гибрид: кандидаты из Qdrant + BM25, реранг по честной BM25-оценке.

    Hashing-векторы дают хороший recall, но шумный порядок; поэтому берём
    объединение кандидатов и сортируем по BM25 (без синонимов), а если
    BM25 пуст — по исходным синонимам и векторному сходству.
    """
    cand = {}
    qdrant_scores = {}
    if getattr(config, "QDRANT_URL", ""):
        try:
            from . import qdrant
            for item in qdrant.search(query, k=max(k * 3, 8)):
                cand[item.get("id")] = item
                qdrant_scores[item.get("id")] = float(item.get("score") or 0)
        except Exception as exc:  # noqa: BLE001
            log.info("qdrant-поиск не сработал: %s", exc)
    for item in INDEX.bm25(query, k=max(k * 3, 8), expand=True):
        cand.setdefault(item.get("id"), item)
    if not cand:
        return INDEX.bm25(query, k=k)
    bm = {item["id"]: item["score"]
          for item in INDEX.bm25(query, k=max(50, len(INDEX.chunks)), expand=False)}
    if not bm:
        bm = {item["id"]: item["score"]
              for item in INDEX.bm25(query, k=max(50, len(INDEX.chunks)), expand=True)}
    out = sorted(cand.values(),
                 key=lambda it: (bm.get(it.get("id"), 0.0),
                                 qdrant_scores.get(it.get("id"), 0.0)),
                 reverse=True)
    return out[:k]


def context(query, k=4, limit=2600):
    """Текст для системного промпта: выдержки из официальных правил."""
    found = search(query, k=k)
    if not found:
        # общий вопрос о правилах: даём базовые разделы о правах и модерации
        base = [c for c in INDEX.chunks
                if (c.get("section") or "").startswith(("Раздел II", "Раздел VII"))]
        found = [{"id": c.get("id"), "section": c.get("section"),
                  "text": c["text"], "score": 0} for c in base[:k]]
    if not found:
        return "", []
    lines = ["ОФИЦИАЛЬНЫЙ СБОРНИК AETHERLYN (выдержки, цитируй раздел):"]
    used = []
    total = 0
    for item in found:
        piece = "• [%s] %s" % (item.get("section") or "Раздел", item["text"].strip())
        if total + len(piece) > limit:
            break
        lines.append(piece)
        used.append(item.get("id"))
        total += len(piece)
    return "\n".join(lines), used


def ready():
    return INDEX.load()
