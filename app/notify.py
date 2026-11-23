"""Outbound notifications to the owner, team chat and applicants."""

from __future__ import annotations

import html
import logging
import traceback

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup

from app.config import Settings
from app.security.redaction import redact_text

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, bot: Bot | None, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings

    @property
    def ready(self) -> bool:
        return self.bot is not None

    async def owner(
        self,
        text: str,
        *,
        markup: InlineKeyboardMarkup | None = None,
        disable_preview: bool = True,
    ) -> bool:
        if self.bot is None:
            return False
        return await self._send(
            self.settings.owner_id, text, markup=markup, disable_preview=disable_preview
        )

    async def team(
        self,
        text: str,
        *,
        markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        if self.bot is None or self.settings.team_chat_id == 0:
            return False
        return await self._send(self.settings.team_chat_id, text, markup=markup)

    async def user(
        self,
        user_id: int,
        text: str,
        *,
        markup: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = "HTML",
    ) -> bool:
        if self.bot is None:
            return False
        return await self._send(user_id, text, markup=markup, parse_mode=parse_mode)

    async def owner_attachment(
        self,
        text: str,
        *,
        file_id: str,
        kind: str | None,
        markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        """Send a card with its screenshot; fall back to a text card if needed."""
        if self.bot is None:
            return False
        caption = text if len(text) <= 1000 else text[:997] + "…"
        try:
            if kind == "document":
                await self.bot.send_document(
                    self.settings.owner_id,
                    document=file_id,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
            elif kind in {"video", "animation"}:
                await self.bot.send_video(
                    self.settings.owner_id,
                    video=file_id,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
            else:
                await self.bot.send_photo(
                    self.settings.owner_id,
                    photo=file_id,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
            return True
        except TelegramAPIError:
            logger.warning("Could not send attachment to owner", exc_info=True)
        return await self.owner(text, markup=markup)

    async def error(self, context: str, exc: BaseException | None = None) -> None:
        detail = ""
        if exc is not None:
            detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-1200:]
        text = (
            f"<b>Ошибка Eva</b>\n{context}\n<pre>{html.escape(redact_text(detail)[-1000:])}</pre>"
        ).strip()
        await self.owner(text)

    async def _send(
        self,
        chat_id: int,
        text: str,
        *,
        markup: InlineKeyboardMarkup | None = None,
        disable_preview: bool = True,
        parse_mode: str | None = "HTML",
    ) -> bool:
        if self.bot is None:
            return False
        clean = text[:4000]
        try:
            await self.bot.send_message(
                chat_id,
                clean,
                parse_mode=parse_mode,
                reply_markup=markup,
                disable_web_page_preview=disable_preview,
            )
            return True
        except TelegramAPIError as exc:
            logger.warning("Telegram send failed: %s", redact_text(str(exc)))
            return False

    async def broadcast(self, user_ids: list[int], text: str) -> tuple[int, int]:
        """Owner-triggered broadcast; every send is best effort and logged."""
        sent = 0
        failed = 0
        for user_id in user_ids:
            if await self.user(user_id, text):
                sent += 1
            else:
                failed += 1
        await self.owner(f"<b>Рассылка завершена</b>\nОтправлено: {sent}\nОшибок: {failed}")
        return sent, failed
