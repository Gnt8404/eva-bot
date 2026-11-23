"""Per-user message/callback throttling with a temporary mute."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.bot.group import is_group
from app.config import Settings
from app.db.models import User
from app.db.session import Database
from app.security.audit import AuditService

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._minute: dict[int, deque[float]] = defaultdict(deque)
        self._hour: dict[int, deque[float]] = defaultdict(deque)

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = getattr(event, "from_user", None)
        if tg_user is None:
            return await handler(event, data)
        settings: Settings = data["settings"]
        if tg_user.id == settings.owner_id:
            return await handler(event, data)
        if isinstance(event, Message) and is_group(event):
            return await handler(event, data)
        if (
            isinstance(event, CallbackQuery)
            and isinstance(event.message, Message)
            and is_group(event.message)
        ):
            return await handler(event, data)
        now = time.monotonic()
        minute = self._minute[tg_user.id]
        hour = self._hour[tg_user.id]
        while minute and now - minute[0] > 60:
            minute.popleft()
        while hour and now - hour[0] > 3600:
            hour.popleft()
        too_many = len(minute) >= settings.throttle_per_minute or (
            len(hour) >= settings.throttle_per_hour
        )
        if too_many:
            await self._mute(data["db"], tg_user.id, settings, data)
            if isinstance(event, Message):
                await event.answer(
                    f"Слишком много сообщений. Мут на {settings.throttle_mute_seconds // 60} мин."
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("Слишком много запросов. Временный мут.", show_alert=True)
            minute.clear()
            hour.clear()
            return None
        minute.append(now)
        hour.append(now)
        return await handler(event, data)

    @staticmethod
    async def _mute(
        database: Database,
        user_id: int,
        settings: Settings,
        data: dict[str, Any],
    ) -> None:
        until = datetime.now(UTC) + timedelta(seconds=settings.throttle_mute_seconds)
        async with database.session() as session:
            user = await session.get(User, user_id)
            if user is not None:
                user.muted_until = until
        audit = data.get("audit")
        if isinstance(audit, AuditService):
            await audit.record(
                actor_id=user_id,
                action="throttle_mute",
                target=str(user_id),
                result="ok",
                details=f"until={until.isoformat()}",
            )
