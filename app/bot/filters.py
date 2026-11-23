"""Reusable aiogram filters and the per-user rate-limit filter."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import Settings


def _user_id(event: TelegramObject) -> int | None:
    user = getattr(event, "from_user", None)
    return getattr(user, "id", None)


class IsOwner(BaseFilter):
    async def __call__(self, event: TelegramObject, settings: Settings) -> bool:
        return _user_id(event) == settings.owner_id


class IsAdmin(BaseFilter):
    async def __call__(self, event: TelegramObject, settings: Settings) -> bool:
        user_id = _user_id(event)
        return user_id is not None and (
            user_id == settings.owner_id or user_id in settings.readonly_admin_ids
        )


class RateLimitFilter(BaseFilter):
    """Small in-memory guard for sensitive handlers."""

    def __init__(self, seconds: float = 2.0) -> None:
        self.seconds = seconds
        self._last: dict[int, float] = defaultdict(float)

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user_id = _user_id(event)
        if user_id is None:
            return False
        now = time.monotonic()
        if now - self._last[user_id] < self.seconds:
            return False
        self._last[user_id] = now
        return True


MiddlewareCall = Callable[[Any, dict[str, Any]], Awaitable[Any]]
