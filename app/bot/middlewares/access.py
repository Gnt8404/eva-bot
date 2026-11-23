"""Blacklist, ban, mute, captcha and optional channel-subscription gate."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from aiogram.types import User as TgUser

from app.bot.group import is_group
from app.bot.keyboards import CB_CAPTCHA, CB_CHECK_SUB, captcha_menu
from app.config import Settings
from app.db.models import User
from app.db.session import Database
from app.utils import as_utc

logger = logging.getLogger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = getattr(event, "from_user", None)
        if tg_user is None:
            return await handler(event, data)
        settings: Settings = data["settings"]
        database: Database = data["db"]
        if tg_user.id in settings.blacklisted_ids:
            return None
        user = await self._upsert(database, tg_user, owner=tg_user.id == settings.owner_id)
        if user.is_banned and tg_user.id != settings.owner_id:
            if isinstance(event, Message):
                await event.answer("Доступ к боту ограничен.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Доступ ограничен.", show_alert=True)
            return None
        if user.muted_until and tg_user.id != settings.owner_id:
            until = as_utc(user.muted_until)
            if until and until > datetime.now(UTC):
                minutes = max(1, int((until - datetime.now(UTC)).total_seconds() // 60) + 1)
                if isinstance(event, Message):
                    await event.answer(f"Временный мут: ещё {minutes} мин.")
                elif isinstance(event, CallbackQuery):
                    await event.answer(f"Временный мут: ещё {minutes} мин.", show_alert=True)
                return None
        # Group members ask Eva directly without a DM captcha. Spam is still
        # limited by ThrottlingMiddleware; the DM captcha gate stays in place.
        group_message = isinstance(event, Message) and is_group(event)
        if not user.captcha_ok and tg_user.id != settings.owner_id and not group_message:
            allowed = False
            if isinstance(event, Message):
                text = (event.text or "").strip()
                allowed = text.startswith("/start")
            elif isinstance(event, CallbackQuery):
                allowed = event.data in {CB_CAPTCHA, CB_CHECK_SUB}
            if not allowed:
                if isinstance(event, Message):
                    await event.answer(
                        "Подтвердите, что вы человек, чтобы продолжить.",
                        reply_markup=captcha_menu(),
                    )
                elif isinstance(event, CallbackQuery):
                    await event.answer("Сначала подтвердите, что вы человек.", show_alert=True)
                return None
        data["db_user"] = user
        return await handler(event, data)

    @staticmethod
    async def _upsert(database: Database, tg_user: TgUser, *, owner: bool) -> User:
        now = datetime.now(UTC)
        async with database.session() as session:
            user = await session.get(User, tg_user.id)
            if user is None:
                user = User(
                    id=tg_user.id,
                    username=tg_user.username,
                    first_name=tg_user.first_name,
                    captcha_ok=owner or False,
                    is_admin=owner,
                    last_seen_at=now,
                )
                session.add(user)
                await session.flush()
                await session.refresh(user)
            else:
                user.username = tg_user.username
                user.first_name = tg_user.first_name
                user.last_seen_at = now
                if owner:
                    user.is_admin = True
                    user.captcha_ok = True
            session.expunge(user)
            return user
