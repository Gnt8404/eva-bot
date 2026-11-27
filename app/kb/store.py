"""SQLite-backed knowledge base: ingestion, stats and search facade."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import FaqOverride, KbChunk, KbPost
from app.db.session import Database
from app.kb.ingest_docs import (
    StaticChunk,
    load_channels,
    load_datapacks,
    load_donation,
    load_plugins,
    load_static_chunks,
)
from app.kb.search import SearchEngine, SearchResponse
from app.kb.text import (
    build_search_text,
    chunk_text,
    embedding_bytes,
    normalize,
)
from app.security.redaction import redact

TAG_RE = re.compile(r"#[A-Za-zА-Яа-яЁё0-9_]+")


@dataclass(slots=True)
class IngestStats:
    documents: int = 0
    chunks: int = 0
    posts: int = 0
    tags: int = 0


def extract_tags(*parts: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for match in TAG_RE.findall(part or ""):
            if match not in seen:
                seen.add(match)
                found.append(match)
    return found


def classify_topic(tags: list[str], text: str) -> str:
    joined = f"{' '.join(tags)} {text}".lower()
    mapping = (
        ("#donate", "donate"),
        ("донат", "donate"),
        ("#sorrow", "sorrow"),
        ("sorrow", "sorrow"),
        ("#rules", "rules"),
        ("правил", "rules"),
        ("#plugins", "plugins"),
        ("плагин", "plugins"),
        ("#season", "season"),
        ("сезон", "season"),
        ("#connect", "connection"),
        ("подключ", "connection"),
    )
    for marker, topic in mapping:
        if marker in joined:
            return topic
    return "news"


class KnowledgeBase:
    """Static server knowledge, channel posts, owner overrides and retrieval."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.search_engine = SearchEngine(database, settings)

    async def static_chunk_count(self) -> int:
        async with self.database.session() as session:
            return int(
                await session.scalar(
                    select(func.count()).select_from(KbChunk).where(KbChunk.doc_id.not_like("tg:%"))
                )
                or 0
            )

    async def ensure_static(self) -> IngestStats:
        if await self.static_chunk_count() > 0:
            return IngestStats()
        return await self.ingest_static(force=False)

    async def ingest_static(self, *, force: bool = True) -> IngestStats:
        chunks = load_static_chunks(self.settings)
        async with self.database.session() as session:
            if force:
                await session.execute(delete(KbChunk).where(KbChunk.doc_id.not_like("tg:%")))
                await session.flush()
            for chunk in chunks:
                self._add_chunk(session, chunk)
            await session.flush()
        return IngestStats(
            documents=len({chunk.doc_id for chunk in chunks}),
            chunks=len(chunks),
            tags=len({tag for chunk in chunks for tag in chunk.tags}),
        )

    def _add_chunk(
        self,
        session: AsyncSession,
        chunk: StaticChunk,
        *,
        source_url: str | None = None,
    ) -> None:
        content = normalize(redact(chunk.content))
        answer = normalize(redact(chunk.answer))
        if not content and not answer:
            return
        search_text = build_search_text(
            chunk.title,
            content,
            answer,
            " ".join(chunk.tags),
        )
        row = KbChunk(
            doc_id=chunk.doc_id[:128],
            chunk_index=chunk.chunk_index,
            title=chunk.title[:255],
            topic=chunk.topic[:64],
            content=content,
            answer=answer,
            source_label=chunk.source_label[:255],
            source_url=(source_url if source_url is not None else chunk.source_url)[:512],
            tags=list(chunk.tags),
            priority=chunk.priority,
            whole=chunk.whole,
            search_text=search_text,
            embedding=embedding_bytes(f"{chunk.title}\n{content}\n{answer}"),
        )
        session.add(row)

    async def ingest_post(
        self,
        *,
        channel: str,
        channel_label: str,
        message_id: int,
        text: str,
        posted_at: datetime | None = None,
        author: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        link: str = "",
        tags: list[str] | None = None,
    ) -> bool:
        body = normalize(redact(text or ""))
        if len(body) < 3:
            return False
        discovered = extract_tags(body)
        all_tags = list(dict.fromkeys([*(tags or []), *discovered, "#Aetherlyn"]))
        doc_id = f"tg:{channel}:{message_id}"[:128]
        async with self.database.session() as session:
            post = await session.scalar(
                select(KbPost).where(
                    KbPost.channel == channel[:128],
                    KbPost.tg_message_id == message_id,
                )
            )
            if post is None:
                post = KbPost(
                    channel=channel[:128],
                    channel_label=channel_label[:128],
                    tg_message_id=message_id,
                )
                session.add(post)
            post.channel_label = channel_label[:128]
            post.posted_at = posted_at
            post.author = (author or "")[:128]
            post.text = body
            post.attachments = attachments or []
            post.link = link[:512]
            post.tags = all_tags
            await session.flush()

            await session.execute(delete(KbChunk).where(KbChunk.doc_id == doc_id))
            await session.flush()
            pieces = chunk_text(
                body,
                size=self.settings.post_chunk_size,
                overlap=self.settings.post_chunk_overlap,
            )
            title = body.splitlines()[0][:120] if body else f"Пост {message_id}"
            label = " ".join(all_tags)[:255]
            for index, piece in enumerate(pieces):
                source_label = label or channel_label
                self._add_chunk(
                    session,
                    StaticChunk(
                        doc_id=doc_id,
                        chunk_index=index,
                        title=title,
                        topic=classify_topic(all_tags, body),
                        content=piece,
                        source_label=source_label,
                        source_url=link,
                        tags=all_tags,
                        priority=65,
                        whole=False,
                    ),
                    source_url=link,
                )
            await session.flush()
        return True

    async def add_override(
        self,
        *,
        question: str,
        answer: str,
        created_by: int,
        keywords: str = "",
        source_label: str = "FAQ (правка владельца)",
    ) -> FaqOverride:
        clean_question = normalize(redact(question))[:500]
        clean_answer = normalize(redact(answer))[:2000]
        if not clean_question or not clean_answer:
            raise ValueError("Пустой вопрос или ответ")
        async with self.database.session() as session:
            row = FaqOverride(
                question=clean_question,
                answer=clean_answer,
                keywords=normalize(redact(keywords))[:1000],
                source_label=source_label[:255],
                created_by=created_by,
            )
            session.add(row)
            await session.flush()
            return row

    async def list_overrides(self, *, limit: int = 50) -> list[FaqOverride]:
        async with self.database.session() as session:
            return list(
                (
                    await session.scalars(
                        select(FaqOverride)
                        .where(FaqOverride.active.is_(True))
                        .order_by(FaqOverride.updated_at.desc())
                        .limit(limit)
                    )
                ).all()
            )

    async def deactivate_override(self, override_id: int) -> bool:
        async with self.database.session() as session:
            row = await session.get(FaqOverride, override_id)
            if row is None:
                return False
            row.active = False
            return True

    async def search(self, question: str, *, top_k: int | None = None) -> SearchResponse:
        return await self.search_engine.search(question, top_k=top_k)

    async def reindex(self) -> IngestStats:
        stats = await self.ingest_static(force=True)
        async with self.database.session() as session:
            posts = list((await session.scalars(select(KbPost))).all())
        posts_done = 0
        for post in posts:
            if await self.ingest_post(
                channel=post.channel,
                channel_label=post.channel_label,
                message_id=post.tg_message_id,
                text=post.text,
                posted_at=post.posted_at,
                author=post.author,
                attachments=post.attachments,
                link=post.link,
                tags=post.tags,
            ):
                posts_done += 1
        stats.posts = posts_done
        stats.chunks = await self.chunk_count()
        return stats

    async def chunk_count(self) -> int:
        async with self.database.session() as session:
            return int(await session.scalar(select(func.count()).select_from(KbChunk)) or 0)

    async def post_count(self) -> int:
        async with self.database.session() as session:
            return int(await session.scalar(select(func.count()).select_from(KbPost)) or 0)

    async def unanswered_count(self) -> int:
        from app.db.models import Unanswered

        async with self.database.session() as session:
            return int(
                await session.scalar(
                    select(func.count()).select_from(Unanswered).where(Unanswered.status == "open")
                )
                or 0
            )

    async def stats(self) -> dict[str, int]:
        return {
            "posts": await self.post_count(),
            "pages": await self.static_chunk_count(),
            "chunks": await self.chunk_count(),
            "unanswered": await self.unanswered_count(),
            "overrides": len(await self.list_overrides(limit=10_000)),
        }

    def donation_config(self) -> dict[str, Any]:
        return load_donation(self.settings)

    def plugins(self, *, include_admin: bool = False) -> list[dict[str, Any]]:
        plugins = load_plugins(self.settings)
        if include_admin:
            return plugins
        return [item for item in plugins if not item.get("admin_only")]

    def datapacks(self) -> list[dict[str, Any]]:
        return load_datapacks(self.settings)

    def channels(self) -> list[dict[str, Any]]:
        return load_channels(self.settings)
