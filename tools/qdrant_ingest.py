# -*- coding: utf-8 -*-
"""Залить RAG-чанки Aetherlyn в Qdrant.

QDRANT_URL=http://127.0.0.1:6333 python tools/qdrant_ingest.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, qdrant, rag  # noqa: E402


def main():
    print("QDRANT_URL:", config.QDRANT_URL or "(не задан)")
    if not qdrant.enabled():
        print("Задай QDRANT_URL (и при необходимости QDRANT_API_KEY).")
        return 2
    rag.INDEX.load()
    print("чанков в RAG:", len(rag.INDEX.chunks))
    ok = qdrant.ingest()
    print("загрузка:", "OK" if ok else "FAIL")
    if ok:
        found = qdrant.search("правила и обязанности игроков", k=3)
        print("поиск:", [(x["id"], x["score"]) for x in found])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
