"""Опциональный Qdrant через REST (stdlib): вставка и поиск чанков RAG."""
import hashlib
import json
import logging
import urllib.error
import urllib.request

from . import config

log = logging.getLogger("qdrant")
TIMEOUT = 10


def _base():
    url = (getattr(config, "QDRANT_URL", "") or "").rstrip("/")
    return url


def _headers():
    head = {"Content-Type": "application/json", "Accept": "application/json"}
    key = getattr(config, "QDRANT_API_KEY", "") or ""
    if key:
        head["api-key"] = key
    return head


def _call(method, path, payload=None, timeout=TIMEOUT):
    url = _base() + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def enabled():
    return bool(_base())


def _collection():
    return getattr(config, "QDRANT_COLLECTION", "aetherlyn_rules")


def ensure_collection(size=256):
    if not enabled():
        return False
    name = _collection()
    try:
        _call("PUT", "/collections/%s" % name,
              {"vectors": {"size": size, "distance": "Cosine"}})
        return True
    except urllib.error.HTTPError as exc:
        if exc.code in (200, 201, 409):
            return True
        # коллекция уже есть с другой конфигурацией — считаем готовой
        try:
            _call("GET", "/collections/%s" % name)
            return True
        except Exception:
            return False
    except Exception as exc:  # noqa: BLE001
        log.info("qdrant недоступен: %s", exc)
        return False


def _point_id(chunk_id):
    return int(hashlib.md5(str(chunk_id).encode("utf-8")).hexdigest()[:12], 16)


def upsert(items, vectors):
    """items: чанки; vectors: список векторов той же длины."""
    if not enabled() or not items:
        return False
    points = []
    for item, vec in zip(items, vectors):
        points.append({"id": _point_id(item.get("id")),
                       "vector": vec,
                       "payload": {"id": item.get("id"),
                                   "section": item.get("section"),
                                   "text": item.get("text")}})
    try:
        _call("PUT", "/collections/%s/points?wait=true" % _collection(),
              {"points": points})
        return True
    except Exception as exc:  # noqa: BLE001
        log.info("qdrant upsert не удался: %s", exc)
        return False


def ingest():
    """Залить чанки из knowledge/aetherlyn_rag.json в Qdrant."""
    from . import rag
    if not enabled() or not rag.INDEX.load():
        return False
    if not ensure_collection():
        return False
    items = rag.INDEX.chunks
    vectors = [rag.hash_vector(i["text"] + " " + (i.get("section") or "")) for i in items]
    return upsert(items, vectors)


def search(query, k=4):
    if not enabled():
        return []
    from . import rag
    try:
        data = _call("POST", "/collections/%s/points/search" % _collection(),
                     {"vector": rag.hash_vector(query), "limit": k,
                      "with_payload": True})
    except Exception as exc:  # noqa: BLE001
        log.info("qdrant search не удался: %s", exc)
        return []
    out = []
    for hit in data.get("result") or []:
        payload = hit.get("payload") or {}
        if not payload.get("text"):
            continue
        out.append({"id": payload.get("id"),
                    "section": payload.get("section"),
                    "text": payload.get("text"),
                    "score": round(float(hit.get("score") or 0), 3),
                    "source": "qdrant"})
    return out
