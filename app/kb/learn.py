"""Feedback loop: unresolved questions become owner-visible learning material."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.db.models import FaqOverride, Unanswered
from app.db.session import Database
from app.kb.store import KnowledgeBase


class LearningService:
    def __init__(self, database: Database, knowledge: KnowledgeBase) -> None:
        self.database = database
        self.knowledge = knowledge

    async def record_unanswered(
        self,
        *,
        user_id: int,
        question: str,
        source: str = "question",
        ticket_id: int | None = None,
    ) -> int:
        async with self.database.session() as session:
            row = Unanswered(
                user_id=user_id,
                question=question[:2000],
                source=source,
                ticket_id=ticket_id,
            )
            session.add(row)
            await session.flush()
            return row.id

    async def mark_resolved(self, unanswered_id: int) -> bool:
        async with self.database.session() as session:
            row = await session.get(Unanswered, unanswered_id)
            if row is None:
                return False
            row.status = "resolved"
            row.resolved_at = datetime.now(UTC)
            return True

    async def apply_owner_answer(
        self,
        *,
        question: str,
        answer: str,
        owner_id: int,
        keywords: str = "",
    ) -> FaqOverride:
        override = await self.knowledge.add_override(
            question=question,
            answer=answer,
            created_by=owner_id,
            keywords=keywords,
        )
        async with self.database.session() as session:
            open_rows = (
                await session.scalars(
                    select(Unanswered)
                    .where(Unanswered.status == "open")
                    .where(func.lower(Unanswered.question).contains(question.lower()[:80]))
                )
            ).all()
            for row in open_rows:
                row.status = "resolved"
                row.resolved_at = datetime.now(UTC)
        return override

    async def top_unanswered(self, limit: int = 10) -> list[dict[str, Any]]:
        async with self.database.session() as session:
            rows = (
                await session.execute(
                    select(
                        Unanswered.question,
                        func.count(Unanswered.id).label("uses"),
                        func.max(Unanswered.created_at).label("last_seen"),
                    )
                    .where(Unanswered.status == "open")
                    .group_by(Unanswered.question)
                    .order_by(func.count(Unanswered.id).desc())
                    .limit(limit)
                )
            ).all()
            return [
                {"question": row.question, "uses": row.uses, "last_seen": row.last_seen}
                for row in rows
            ]

    async def weekly_report(self) -> dict[str, Any]:
        since = datetime.now(UTC) - timedelta(days=7)
        async with self.database.session() as session:
            added = int(
                await session.scalar(
                    select(func.count())
                    .select_from(FaqOverride)
                    .where(FaqOverride.created_at >= since)
                )
                or 0
            )
            open_questions = int(
                await session.scalar(
                    select(func.count()).select_from(Unanswered).where(Unanswered.status == "open")
                )
                or 0
            )
        return {
            "overrides_added": added,
            "open_questions": open_questions,
            "top_unanswered": await self.top_unanswered(10),
        }
