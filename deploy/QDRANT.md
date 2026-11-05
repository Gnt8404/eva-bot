# Qdrant для Евы

Ева умеет хранить RAG-чанки в Qdrant и искать по ним (REST, без внешних
библиотек в рантайме). Если `QDRANT_URL` не задан, используется встроенный
BM25 по `knowledge/aetherlyn_rag.json` — бот работает без Qdrant.

## Вариант 1. Локально (Windows)
```
# скачать бинарник
https://github.com/qdrant/qdrant/releases/latest/download/qdrant-x86_64-pc-windows-msvc.zip
# распаковать, запустить qdrant.exe (порт 6333 по умолчанию)
qdrant.exe
```
Затем в `.env`:
```
QDRANT_URL=http://127.0.0.1:6333
QDRANT_COLLECTION=aetherlyn_rules
```
Залить чанки:
```
python tools/qdrant_ingest.py
```
Проверить: `curl http://127.0.0.1:6333/collections/aetherlyn_rules`.

## Вариант 2. Qdrant Cloud (бесплатный кластер, для облачной Евы)
1. https://cloud.qdrant.io → создать бесплатный кластер (1 GB free).
2. Взять Cluster URL и API Key.
3. В Render → Environment добавить:
```
QDRANT_URL=https://<cluster>.cloud.qdrant.io
QDRANT_API_KEY=<api-key>
QDRANT_COLLECTION=aetherlyn_rules
```
4. Перезапустить сервис; бот при старте зальёт чанки (или запустить
   `python tools/qdrant_ingest.py` локально, указав те же переменные).

## Вариант 3. Свой сервер/Render
Qdrant можно поднять как Docker-сервис (`qdrant/qdrant`) с API-ключом
`QDRANT__SERVICE__API_KEY`. На free-плане Render лимит 750 часов на аккаунт,
а Ева уже занимает почти всё — для 24/7 лучше Qdrant Cloud free.

## MCP
MCP-сервер Qdrant (`mcp-server-qdrant`) умеет работать через
`QDRANT_URL`/`QDRANT_API_KEY` или локальный путь `QDRANT_LOCAL_PATH`.
Ева действия (тикеты, перезапуск, баны) выполняет своими инструментами
(`app/tools.py`), MCP ей для этого не нужен; MCP полезен coding-агенту.
