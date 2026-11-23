"""Load the curated server documents and turn them into KB chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.config import Settings
from app.kb.text import chunk_text


@dataclass(slots=True)
class StaticChunk:
    doc_id: str
    chunk_index: int
    title: str
    topic: str
    content: str
    answer: str = ""
    source_label: str = ""
    source_url: str = ""
    tags: list[str] = field(default_factory=list)
    priority: int = 0
    whole: bool = False


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"Knowledge file is missing: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise RuntimeError(f"Knowledge file must contain a mapping: {path}")
    return data


def load_channels(settings: Settings) -> list[dict[str, Any]]:
    data = read_yaml(settings.kb_dir / "channels.yaml")
    channels = data.get("channels") or []
    if not isinstance(channels, list):
        raise RuntimeError("channels.yaml: 'channels' must be a list")
    return [item for item in channels if isinstance(item, dict)]


def load_plugins(settings: Settings) -> list[dict[str, Any]]:
    data = read_yaml(settings.kb_dir / "plugins.yaml")
    plugins = data.get("plugins") or []
    if not isinstance(plugins, list):
        raise RuntimeError("plugins.yaml: 'plugins' must be a list")
    return [item for item in plugins if isinstance(item, dict)]


def load_datapacks(settings: Settings) -> list[dict[str, Any]]:
    data = read_yaml(settings.kb_dir / "datapacks.yaml")
    datapacks = data.get("datapacks") or []
    if not isinstance(datapacks, list):
        raise RuntimeError("datapacks.yaml: 'datapacks' must be a list")
    return [item for item in datapacks if isinstance(item, dict)]


def load_donation(settings: Settings) -> dict[str, Any]:
    data = read_yaml(settings.kb_dir / "donate.yaml")
    packages = data.get("packages") or []
    if not packages:
        raise RuntimeError("donate.yaml: no packages configured")
    return data


def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


def _core_chunks(data: dict[str, Any]) -> list[StaticChunk]:
    chunks: list[StaticChunk] = []
    for index, document in enumerate(data.get("documents") or []):
        if not isinstance(document, dict):
            continue
        doc_id = str(document.get("id") or f"core-{index}")
        content = str(document.get("content") or "").strip()
        if not content:
            continue
        whole = bool(document.get("whole", False))
        pieces = [content] if whole else chunk_text(content, 700, 80)
        for piece_index, piece in enumerate(pieces):
            chunks.append(
                StaticChunk(
                    doc_id=doc_id,
                    chunk_index=piece_index,
                    title=str(document.get("title") or doc_id),
                    topic=str(document.get("topic") or "general"),
                    content=piece,
                    answer=str(document.get("summary") or "") if whole else "",
                    source_label=str(document.get("source_label") or "#Aetherlyn"),
                    source_url=str(document.get("source_url") or ""),
                    tags=[str(tag) for tag in document.get("tags") or []],
                    priority=int(document.get("priority") or 50),
                    whole=whole,
                )
            )
    return chunks


def _faq_chunks(data: dict[str, Any]) -> list[StaticChunk]:
    chunks: list[StaticChunk] = []
    for index, entry in enumerate(data.get("entries") or []):
        if not isinstance(entry, dict):
            continue
        question = str(entry.get("question") or "").strip()
        answer = str(entry.get("answer") or "").strip()
        if not question or not answer:
            continue
        entry_id = str(entry.get("id") or f"faq-{index}")
        keywords = " ".join(str(item) for item in entry.get("keywords") or [])
        content = f"Вопрос: {question}\nОтвет: {answer}"
        if keywords:
            content += f"\nКлючевые слова: {keywords}"
        chunks.append(
            StaticChunk(
                doc_id=f"faq:{entry_id}",
                chunk_index=0,
                title=question,
                topic=str(entry.get("topic") or "faq"),
                content=content,
                answer=answer,
                source_label=str(entry.get("source_label") or "#Aetherlyn"),
                source_url=str(entry.get("source_url") or ""),
                tags=["#FAQ", f"#{entry.get('topic') or 'faq'}"],
                priority=int(entry.get("priority") or 80),
                whole=True,
            )
        )
    return chunks


def _asset_chunks(
    items: list[dict[str, Any]], *, kind: str, source_label: str
) -> list[StaticChunk]:
    chunks: list[StaticChunk] = []
    for index, item in enumerate(items):
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        version = str(item.get("version") or "").strip()
        description = str(item.get("description") or "").strip()
        title = f"{kind} {name}"
        suffix = f" {version}" if version else ""
        body = f"{name}{suffix} — {description}" if description else f"{name}{suffix}"
        chunks.append(
            StaticChunk(
                doc_id=f"{kind.lower()}:{_slug(name, str(index))}",
                chunk_index=0,
                title=title,
                topic="plugins",
                content=body,
                answer=body,
                source_label=source_label,
                source_url="https://t.me/+utbI0nXnzAhmMWYy",
                tags=["#Aetherlyn", "#plugins"],
                priority=55,
                whole=True,
            )
        )
    return chunks


def _channel_chunk(channels: list[dict[str, Any]]) -> StaticChunk:
    lines = [
        f"{item.get('label', 'Канал')}: {item.get('purpose', '')}".strip()
        + (f" — {item.get('ref')}" if item.get("ref") else "")
        for item in channels
    ]
    return StaticChunk(
        doc_id="channels:all",
        chunk_index=0,
        title="Официальные каналы и чаты Aetherlyn",
        topic="channels",
        content="\n".join(lines),
        source_label="#Aetherlyn #channels",
        source_url="https://t.me/+utbI0nXnzAhmMWYy",
        tags=["#Aetherlyn", "#channels"],
        priority=80,
        whole=True,
    )


def _donation_chunk(donation: dict[str, Any]) -> StaticChunk:
    message = str(donation.get("message") or "").strip()
    return StaticChunk(
        doc_id="donate:table",
        chunk_index=0,
        title="Донат: полная таблица цен",
        topic="donate",
        content=message,
        source_label=str(donation.get("source_label") or "#Aetherlyn #donate"),
        source_url=str(donation.get("source_url") or ""),
        tags=["#Aetherlyn", "#donate"],
        priority=100,
        whole=True,
    )


def load_static_chunks(settings: Settings) -> list[StaticChunk]:
    chunks: list[StaticChunk] = []
    chunks.extend(_core_chunks(read_yaml(settings.kb_dir / "core.yaml")))
    chunks.extend(_faq_chunks(read_yaml(settings.kb_dir / "faq.yaml")))
    chunks.extend(
        _asset_chunks(
            load_plugins(settings),
            kind="Плагин",
            source_label="#Aetherlyn #plugins",
        )
    )
    chunks.extend(
        _asset_chunks(
            load_datapacks(settings),
            kind="Датапак",
            source_label="#Aetherlyn #datapacks",
        )
    )
    channels = load_channels(settings)
    if channels:
        chunks.append(_channel_chunk(channels))
    donation = load_donation(settings)
    chunks.append(_donation_chunk(donation))
    return chunks
