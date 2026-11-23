"""Hybrid retrieval: FTS5/BM25 candidates fused with local vectors via RRF."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import FaqOverride, KbChunk
from app.db.session import Database
from app.kb.text import (
    build_search_text,
    coverage,
    embed,
    embedding_from_bytes,
    stems,
)


@dataclass(slots=True)
class SearchResult:
    id: int
    doc_id: str
    title: str
    content: str
    answer: str
    source_label: str
    source_url: str
    topic: str
    tags: list[str] = field(default_factory=list)
    priority: int = 0
    score: float = 0.0
    confidence: float = 0.0
    title_confidence: float = 0.0
    from_override: bool = False


@dataclass(slots=True)
class SearchResponse:
    results: list[SearchResult]
    confidence: float
    reason: str
    query_stems: list[str] = field(default_factory=list)


class SearchEngine:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    async def _override_result(
        self, session: AsyncSession, query_stems: list[str]
    ) -> SearchResult | None:
        rows = (
            await session.scalars(
                select(FaqOverride)
                .where(FaqOverride.active.is_(True))
                .order_by(FaqOverride.updated_at.desc())
            )
        ).all()
        best: tuple[float, FaqOverride] | None = None
        for row in rows:
            indexed = build_search_text(row.question, row.keywords)
            confidence = coverage(query_stems, indexed)
            if confidence < self.settings.min_search_confidence:
                continue
            if best is None or confidence > best[0]:
                best = (confidence, row)
        if best is None:
            return None
        confidence, row = best
        return SearchResult(
            id=0,
            doc_id=f"override:{row.id}",
            title=row.question,
            content=row.answer,
            answer=row.answer,
            source_label=row.source_label,
            source_url="https://t.me/+utbI0nXnzAhmMWYy",
            topic="faq_override",
            tags=["#FAQ", "#owner"],
            priority=110,
            score=1.0,
            confidence=confidence,
            from_override=True,
        )

    async def search(
        self, question: str, *, top_k: int | None = None, session: AsyncSession | None = None
    ) -> SearchResponse:
        query_stems = stems(question)
        if not query_stems:
            return SearchResponse([], 0.0, "empty_query", [])
        if session is None:
            async with self.database.session() as own_session:
                return await self.search(question, top_k=top_k, session=own_session)
        override = await self._override_result(session, query_stems)
        if override is not None:
            return SearchResponse([override], override.confidence, "owner_override", query_stems)

        limit = top_k or self.settings.search_top_k
        fts_ids = await self._fts_ids(session, query_stems)
        vector_ids, _vector_scores = await self._vector_ids(session, question)

        candidate_ids = list(dict.fromkeys([*fts_ids, *vector_ids]))
        if not candidate_ids:
            return SearchResponse([], 0.0, "no_results", query_stems)
        rows = (await session.scalars(select(KbChunk).where(KbChunk.id.in_(candidate_ids)))).all()
        by_id = {row.id: row for row in rows}
        fts_rank = {chunk_id: rank for rank, chunk_id in enumerate(fts_ids)}
        vector_rank = {chunk_id: rank for rank, chunk_id in enumerate(vector_ids)}

        ranked: list[SearchResult] = []
        for chunk_id in candidate_ids:
            chunk = by_id.get(chunk_id)
            if chunk is None:
                continue
            confidence = coverage(query_stems, chunk.search_text)
            if confidence < self.settings.min_search_confidence:
                continue
            title_confidence = coverage(query_stems, build_search_text(chunk.title))
            score = 0.0
            if chunk_id in fts_rank:
                score += 1.0 / (61 + fts_rank[chunk_id])
            if chunk_id in vector_rank:
                score += 1.0 / (61 + vector_rank[chunk_id])
            score += chunk.priority / 100_000.0
            ranked.append(
                SearchResult(
                    id=chunk.id,
                    doc_id=chunk.doc_id,
                    title=chunk.title,
                    content=chunk.content,
                    answer=chunk.answer,
                    source_label=chunk.source_label,
                    source_url=chunk.source_url,
                    topic=chunk.topic,
                    tags=list(chunk.tags or []),
                    priority=chunk.priority,
                    score=score,
                    confidence=confidence,
                    title_confidence=title_confidence,
                )
            )
        if not ranked:
            best = max(
                (
                    coverage(query_stems, by_id[chunk_id].search_text)
                    for chunk_id in candidate_ids
                    if chunk_id in by_id
                ),
                default=0.0,
            )
            return SearchResponse([], best, "low_confidence", query_stems)
        ranked.sort(
            key=lambda item: (
                round(item.confidence, 2),
                round(item.title_confidence, 2),
                item.priority,
                item.score,
            ),
            reverse=True,
        )
        best_confidence = ranked[0].confidence
        return SearchResponse(ranked[:limit], best_confidence, "ok", query_stems)

    async def _fts_ids(self, session: AsyncSession, query_stems: list[str]) -> list[int]:
        terms = [term for term in query_stems[:12] if term and all(char.isalnum() for char in term)]
        if not terms:
            return []
        match = " OR ".join(f'"{term}"*' for term in terms)
        statement = text(
            "SELECT c.id FROM kb_chunks_fts "
            "JOIN kb_chunks c ON c.id = kb_chunks_fts.rowid "
            "WHERE kb_chunks_fts MATCH :match "
            "ORDER BY bm25(kb_chunks_fts) ASC LIMIT 40"
        )
        try:
            result = await session.execute(statement, {"match": match})
            return [int(row[0]) for row in result.all()]
        except Exception:
            return []

    async def _vector_ids(
        self, session: AsyncSession, question: str
    ) -> tuple[list[int], dict[int, float]]:
        query_vector = embed(question)
        rows = (await session.execute(select(KbChunk.id, KbChunk.embedding))).all()
        scores: list[tuple[float, int]] = []
        for chunk_id, payload in rows:
            vector = embedding_from_bytes(payload)
            if vector is None:
                continue
            score = float(vector @ query_vector)
            if score > 0.05:
                scores.append((score, int(chunk_id)))
        scores.sort(reverse=True)
        top = scores[:20]
        return [chunk_id for _, chunk_id in top], {chunk_id: score for score, chunk_id in top}
