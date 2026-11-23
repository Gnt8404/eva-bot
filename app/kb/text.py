"""Text normalization, lightweight Russian stemming and local embeddings.

The knowledge base deliberately avoids paid embedding APIs.  Retrieval combines
SQLite FTS5 (BM25 order) with a hashed character/word vector; both are produced
from exactly the same normalized text, so an index remains valid across restarts.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

import numpy as np

EMBEDDING_DIM = 384
ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
WORD = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)

STOPWORDS = {
    "а",
    "без",
    "более",
    "бы",
    "был",
    "была",
    "были",
    "было",
    "быть",
    "будет",
    "будем",
    "буду",
    "будут",
    "в",
    "вам",
    "вас",
    "весь",
    "во",
    "вот",
    "все",
    "всего",
    "всех",
    "вы",
    "где",
    "да",
    "дай",
    "даже",
    "делать",
    "для",
    "до",
    "его",
    "ее",
    "если",
    "есть",
    "еще",
    "же",
    "за",
    "здесь",
    "и",
    "из",
    "или",
    "им",
    "их",
    "к",
    "как",
    "какая",
    "какие",
    "какой",
    "когда",
    "кто",
    "куда",
    "ли",
    "либо",
    "мне",
    "может",
    "можно",
    "мой",
    "нужно",
    "моя",
    "мы",
    "на",
    "надо",
    "не",
    "него",
    "нее",
    "нет",
    "ни",
    "них",
    "но",
    "ну",
    "о",
    "об",
    "однако",
    "он",
    "она",
    "они",
    "оно",
    "от",
    "очень",
    "по",
    "под",
    "пожалуйста",
    "покажи",
    "при",
    "подскажите",
    "с",
    "со",
    "так",
    "также",
    "такие",
    "такое",
    "такой",
    "там",
    "тебе",
    "тебя",
    "тем",
    "то",
    "тогда",
    "того",
    "тоже",
    "той",
    "только",
    "том",
    "ты",
    "у",
    "уже",
    "хотя",
    "хочу",
    "чего",
    "чей",
    "чем",
    "что",
    "чтобы",
    "чья",
    "эта",
    "эти",
    "это",
    "расскажи",
    "расскажите",
    "сделать",
    "я",
    "a",
    "an",
    "and",
    "are",
    "is",
    "of",
    "the",
    "to",
    "in",
}

SUFFIXES = (
    "иями",
    "ями",
    "ами",
    "ыми",
    "ими",
    "ов",
    "ев",
    "ий",
    "ый",
    "ая",
    "ое",
    "ые",
    "ей",
    "ой",
    "ам",
    "ям",
    "ах",
    "ях",
    "ом",
    "ем",
    "ую",
    "юю",
    "ит",
    "ет",
    "ут",
    "ют",
    "ат",
    "ят",
    "ал",
    "ил",
    "ла",
    "ло",
    "ли",
    "у",
    "ю",
    "а",
    "я",
    "ы",
    "и",
    "е",
    "о",
    "ь",
)


def normalize(text: str) -> str:
    """Normalize untrusted text without destroying its meaning."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.translate(ZERO_WIDTH)
    text = CONTROL.sub(" ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def stem(token: str) -> str:
    """Very small Russian/English suffix stripper.

    It is intentionally conservative: wiki-grade morphology is unnecessary for
    FTS, while over-stemming could merge unrelated server terms.
    """
    token = token.lower().replace("ё", "е")
    if len(token) <= 4 or token.isdigit():
        return token
    for suffix in SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def words(text: str) -> list[str]:
    return [m.group(0).lower() for m in WORD.finditer(normalize(text))]


def keywords(text: str) -> list[str]:
    """Content words in original form, duplicates removed, order kept."""
    seen: set[str] = set()
    result: list[str] = []
    for word in words(text):
        if word in STOPWORDS or len(word) < 2:
            continue
        if word not in seen:
            seen.add(word)
            result.append(word)
    return result


def stems(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for word in keywords(text):
        value = stem(word)
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def build_search_text(*parts: str | None) -> str:
    """Stemmed index text; metadata and content are indexed alike."""
    merged = " ".join(part for part in parts if part)
    return " ".join(stems(merged))


def coverage(query_stems: list[str], indexed: str) -> float:
    if not query_stems:
        return 0.0
    present = set(indexed.split())
    return sum(1 for item in query_stems if item in present) / len(query_stems)


def _hash_feature(value: str) -> tuple[int, float]:
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest()
    number = int.from_bytes(digest, "big")
    index = number % EMBEDDING_DIM
    sign = 1.0 if (number >> 63) & 1 else -1.0
    return index, sign


def embed(text: str) -> np.ndarray:
    """Hashed word + character trigram embedding, L2-normalized float32."""
    normalized = normalize(text).lower().replace("ё", "е")
    vector = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    for token in words(normalized):
        if token in STOPWORDS or len(token) < 2:
            continue
        index, sign = _hash_feature("w:" + stem(token))
        vector[index] += sign
        compact = f" {token} "
        for offset in range(max(0, len(compact) - 3)):
            trigram = compact[offset : offset + 3]
            if trigram.strip():
                char_index, char_sign = _hash_feature("c:" + trigram)
                vector[char_index] += 0.35 * char_sign
    norm = float(np.linalg.norm(vector))
    if norm > 0:
        vector /= norm
    return vector


def embedding_bytes(text: str) -> bytes:
    return embed(text).tobytes()


def embedding_from_bytes(data: bytes | None) -> np.ndarray | None:
    if not data:
        return None
    array = np.frombuffer(data, dtype=np.float32)
    if array.size != EMBEDDING_DIM:
        return None
    return array


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(left, right))


def chunk_text(text: str, size: int = 700, overlap: int = 80) -> list[str]:
    """Split prose into 400-800-ish pieces on paragraph/sentence boundaries."""
    normalized = normalize(text)
    if not normalized:
        return []
    if len(normalized) <= size:
        return [normalized]
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", normalized):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= size:
            units.append(paragraph)
            continue
        sentences = re.split(r"(?<=[.!?…])\s+", paragraph)
        buffer = ""
        for sentence in sentences:
            if len(buffer) + len(sentence) + 1 <= size:
                buffer = f"{buffer} {sentence}".strip()
            else:
                if buffer:
                    units.append(buffer)
                if len(sentence) > size:
                    for start in range(0, len(sentence), size):
                        units.append(sentence[start : start + size])
                    buffer = ""
                else:
                    buffer = sentence
        if buffer:
            units.append(buffer)

    chunks: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
        elif len(current) + len(unit) + 2 <= size:
            current = f"{current}\n\n{unit}"
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n{unit}".strip() if tail else unit
    if current:
        chunks.append(current)
    return chunks
