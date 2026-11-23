"""Structured request logging without message bodies or secrets."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger("eva.updates")
Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class LoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        started = time.perf_counter()
        tg_user = getattr(event, "from_user", None)
        kind = (
            "message"
            if isinstance(event, Message)
            else "callback"
            if isinstance(event, CallbackQuery)
            else "other"
        )
        try:
            return await handler(event, data)
        except Exception:
            logger.exception("handler failed kind=%s user=%s", kind, getattr(tg_user, "id", None))
            raise
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            logger.info(
                "update kind=%s user=%s elapsed_ms=%.1f",
                kind,
                getattr(tg_user, "id", None),
                elapsed,
            )
