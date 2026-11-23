"""Daily and weekly owner reports. Facts only, no invented metrics."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.config import Settings
from app.db.models import Donation, FaqOverride, StatusSample, Unanswered, User
from app.db.session import Database
from app.kb.store import KnowledgeBase
from app.notify import Notifier
from app.tickets.model import DonationStatus

logger = logging.getLogger(__name__)


class DigestService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        kb: KnowledgeBase,
        notifier: Notifier,
    ) -> None:
        self.database = database
        self.settings = settings
        self.kb = kb
        self.notifier = notifier

    async def daily(self) -> str:
        now = datetime.now(UTC)
        since = now - timedelta(hours=24)
        async with self.database.session() as session:
            peak = await session.scalar(
                select(func.max(StatusSample.online)).where(StatusSample.created_at >= since)
            )
            new_users = int(
                await session.scalar(
                    select(func.count()).select_from(User).where(User.created_at >= since)
                )
                or 0
            )
            income_rub = float(
                await session.scalar(
                    select(func.coalesce(func.sum(Donation.amount_rub), 0)).where(
                        Donation.status == DonationStatus.CONFIRMED.value,
                        Donation.confirmed_at >= since,
                    )
                )
                or 0
            )
            income_usd = float(
                await session.scalar(
                    select(func.coalesce(func.sum(Donation.amount_usd), 0.0)).where(
                        Donation.status == DonationStatus.CONFIRMED.value,
                        Donation.confirmed_at >= since,
                    )
                )
                or 0
            )
            unanswered = list(
                (
                    await session.scalars(
                        select(Unanswered)
                        .where(Unanswered.status == "open")
                        .order_by(Unanswered.created_at.desc())
                        .limit(10)
                    )
                ).all()
            )
            open_questions = int(
                await session.scalar(
                    select(func.count()).select_from(Unanswered).where(Unanswered.status == "open")
                )
                or 0
            )
        stats = await self.kb.stats()
        lines = [
            "<b>Дайджест Aetherlyn за 24 часа</b>",
            f"Пик онлайна: {int(peak or 0)}.",
            f"Новые игроки: {new_users}.",
            f"Подтверждённые донаты: {income_rub:.2f} ₽ / {income_usd:.2f} $.",
            f"База знаний: постов {stats['posts']}, чанков {stats['chunks']}, "
            f"оверрайдов {stats['overrides']}.",
            f"Неотвеченных вопросов: {open_questions}.",
        ]
        if unanswered:
            lines.append("Последние неотвеченные:")
            for row in unanswered[:10]:
                lines.append(f"• {row.question[:160]}")
        return "\n".join(lines)

    async def weekly(self) -> str:
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
            resolved = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Unanswered)
                    .where(
                        Unanswered.status == "resolved",
                        Unanswered.resolved_at >= since,
                    )
                )
                or 0
            )
            open_count = int(
                await session.scalar(
                    select(func.count()).select_from(Unanswered).where(Unanswered.status == "open")
                )
                or 0
            )
        stats = await self.kb.stats()
        return (
            "<b>Отчёт обучения за 7 дней</b>\n"
            f"Добавлено оверрайдов: {added}.\n"
            f"Закрыто неотвеченных: {resolved}.\n"
            f"Осталось неотвеченных: {open_count}.\n"
            f"Всего чанков: {stats['chunks']}, постов: {stats['posts']}."
        )

    async def send_daily(self) -> None:
        await self.notifier.owner(await self.daily())

    async def send_weekly(self) -> None:
        await self.notifier.owner(await self.weekly())
