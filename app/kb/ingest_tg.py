"""Read-only Telethon ingestion of Aetherlyn Telegram channels.

Run:
    python -m app.kb.ingest_tg --full
    python -m app.kb.ingest_tg            # incremental, last 200 messages per channel
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC
from typing import Any

from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.db.models import KbPost
from app.db.session import Database
from app.kb.ingest_docs import load_channels
from app.kb.store import KnowledgeBase
from app.logging_setup import configure_logging

logger = logging.getLogger(__name__)


def _normalize_title(value: str) -> str:
    return " ".join(value.casefold().replace("⚚", " ").split())


def _message_text(message: Any) -> str:
    parts: list[str] = []
    if getattr(message, "text", None):
        parts.append(str(message.text))
    if getattr(message, "caption", None):
        parts.append(str(message.caption))
    return "\n".join(part.strip() for part in parts if str(part).strip())


def _attachments(message: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    media = getattr(message, "media", None)
    if media is None:
        return result
    kind = type(media).__name__
    document = getattr(message, "document", None)
    photo = getattr(message, "photo", None)
    result.append(
        {
            "kind": kind,
            "has_document": document is not None,
            "has_photo": photo is not None,
        }
    )
    return result


def _post_link(entity: Any, message_id: int) -> str:
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    entity_id = getattr(entity, "id", 0)
    return f"https://t.me/c/{entity_id}/{message_id}"


async def _resolve_dialog(client: Any, config: dict[str, Any]) -> Any | None:
    label = _normalize_title(str(config.get("label") or ""))
    ref = str(config.get("ref") or "").strip()
    if ref and not ref.startswith("https://t.me/+"):
        try:
            return await client.get_entity(ref)
        except Exception:
            logger.info("Channel ref not resolvable, trying title")
    async for dialog in client.iter_dialogs():
        title = _normalize_title(str(getattr(dialog, "name", "") or ""))
        if label and (label in title or title in label):
            return dialog.entity
    return None


async def _last_message_id(database: Database, channel: str) -> int:
    async with database.session() as session:
        return int(
            await session.scalar(
                select(func.max(KbPost.tg_message_id)).where(KbPost.channel == channel)
            )
            or 0
        )


async def ingest_channels(
    settings: Settings,
    database: Database,
    *,
    full: bool = False,
) -> dict[str, int]:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if not settings.telethon_ready:
        raise RuntimeError("Telethon is not configured: set TG_API_ID, TG_API_HASH and TG_SESSION")
    kb = KnowledgeBase(database, settings)
    await kb.ensure_static()
    totals = {"channels": 0, "posts": 0, "skipped": 0}
    client = TelegramClient(
        StringSession(settings.tg_session.get_secret_value()),
        settings.tg_api_id,
        settings.tg_api_hash.get_secret_value(),
    )
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telethon StringSession is not authorized")
        for config in load_channels(settings):
            channel_key = str(config.get("key") or "channel")
            channel_label = str(config.get("label") or channel_key)
            entity = await _resolve_dialog(client, config)
            if entity is None:
                logger.warning("Channel not found in dialogs: %s", channel_label)
                totals["skipped"] += 1
                continue
            totals["channels"] += 1
            min_id = 0 if full else await _last_message_id(database, channel_key)
            count = 0
            async for message in client.iter_messages(
                entity,
                min_id=min_id,
                reverse=full,
                limit=None if full else 200,
            ):
                text = _message_text(message)
                if not text:
                    continue
                posted_at = getattr(message, "date", None)
                if posted_at is not None:
                    posted_at = posted_at.astimezone(UTC)
                author = None
                sender = getattr(message, "sender", None)
                if sender is not None:
                    author = getattr(sender, "username", None) or getattr(
                        sender, "first_name", None
                    )
                stored = await kb.ingest_post(
                    channel=channel_key,
                    channel_label=channel_label,
                    message_id=int(message.id),
                    text=text,
                    posted_at=posted_at,
                    author=author,
                    attachments=_attachments(message),
                    link=_post_link(entity, int(message.id)),
                )
                if stored:
                    totals["posts"] += 1
                    count += 1
            logger.info("Ingested %s posts from %s", count, channel_label)
    finally:
        await client.disconnect()
    return totals


async def _run(full: bool) -> None:
    settings = get_settings()
    configure_logging(settings)
    database = Database.from_settings(settings)
    await database.init_models()
    try:
        result = await ingest_channels(settings, database, full=full)
        print(
            "Ингест завершён: "
            f"каналов {result['channels']}, постов {result['posts']}, пропущено {result['skipped']}"
        )
        kb = KnowledgeBase(database, settings)
        stats = await kb.stats()
        print(
            f"В базе: постов {stats['posts']}, страниц {stats['pages']}, чанков {stats['chunks']}"
        )
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Telegram channel ingestion")
    parser.add_argument("--full", action="store_true", help="Read the complete channel history")
    args = parser.parse_args()
    asyncio.run(_run(args.full))


if __name__ == "__main__":
    main()
