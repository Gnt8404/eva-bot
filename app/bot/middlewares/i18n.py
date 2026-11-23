"""Single-locale (ru) middleware kept as a seam for future translations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]

_MESSAGES = {
    "error": "Не удалось обработать запрос. Попробуйте позже.",
    "cancelled": "Действие отменено.",
}


class I18nMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["locale"] = "ru"

        def translate(key: str) -> str:
            return _MESSAGES.get(key, key)

        data["_"] = translate
        return await handler(event, data)
