# -*- coding: utf-8 -*-
"""MCP-сервер Qdrant для Aetherlyn (stdio, JSON-RPC 2.0).

Запуск:
    QDRANT_URL=http://127.0.0.1:6333 python tools/mcp_qdrant.py

Подключение в MCP-клиенте (пример .mcp.json):
{
  "mcpServers": {
    "qdrant-aetherlyn": {
      "command": "python",
      "args": ["C:/путь/к/mc-eva-bot-v6.4/tools/mcp_qdrant.py"],
      "env": {"QDRANT_URL": "http://127.0.0.1:6333"}
    }
  }
}

Инструменты: search_rules (поиск по сборнику Aetherlyn), list_sections,
stats. Если Qdrant недоступен — поиск автоматически идёт по встроенному
BM25 (app.rag), поэтому сервер не падает.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import qdrant, rag  # noqa: E402

TOOLS = [
    {
        "name": "search_rules",
        "description": "Поиск по официальному сборнику прав и обязанностей Aetherlyn.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "вопрос или фраза"},
                "k": {"type": "integer", "description": "сколько фрагментов вернуть", "default": 4},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_sections",
        "description": "Список разделов официального сборника Aetherlyn.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "stats",
        "description": "Статистика RAG/Qdrant: число чанков и доступность Qdrant.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(msg_id, result):
    _send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(msg_id, code, message):
    _send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def _call_tool(name, args):
    if name == "search_rules":
        query = str(args.get("query") or "").strip()
        k = int(args.get("k") or 4)
        if not query:
            return "Пустой запрос."
        found = qdrant.search(query, k=k) or rag.search(query, k=k)
        if not found:
            return "Ничего не найдено в официальных документах Aetherlyn."
        lines = []
        for item in found:
            lines.append("[%s] %s\n%s" % (item.get("id"), item.get("section") or "",
                                          item.get("text", "")[:900]))
        return "\n\n".join(lines)
    if name == "list_sections":
        rag.INDEX.load()
        seen = []
        for chunk in rag.INDEX.chunks:
            section = chunk.get("section") or ""
            if section and section not in seen:
                seen.append(section)
        return "\n".join(seen) or "Разделы не загружены."
    if name == "stats":
        rag.INDEX.load()
        return json.dumps({"chunks": len(rag.INDEX.chunks),
                           "qdrant": bool(qdrant.enabled())}, ensure_ascii=False)
    return "Неизвестный инструмент: %s" % name


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            _result(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "qdrant-aetherlyn", "version": "1.0"},
            })
        elif method in ("notifications/initialized", "initialized"):
            continue
        elif method == "tools/list":
            _result(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            name = params.get("name") or ""
            args = params.get("arguments") or {}
            try:
                text = _call_tool(name, args)
                _result(msg_id, {"content": [{"type": "text", "text": text}]})
            except Exception as exc:  # noqa: BLE001
                _result(msg_id, {"content": [{"type": "text", "text": "Ошибка: %s" % exc}],
                                 "isError": True})
        elif method == "ping":
            _result(msg_id, {})
        else:
            _error(msg_id, -32601, "method not found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
