"""Last-resort AI router. It never executes tools and never trusts user text."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from app.bot.keyboards import question_decision
from app.llm.service import AnswerKind, AssistantService
from app.notify import Notifier
from app.tickets.cards import question_card
from app.tickets.service import TicketService

logger = logging.getLogger(__name__)
router = Router(name="ai")

RESERVED = {
    "Нужна помощь",
    "Жалоба на игрока",
    "О сервере",
    "Плагины и датапаки",
    "Анкета",
    "Донаты",
    "Консоль",
}


async def _process(
    message: Message,
    question: str,
    assistant: AssistantService,
    notifier: Notifier,
    tickets: TicketService,
) -> None:
    try:
        result = await assistant.answer(message.from_user.id if message.from_user else 0, question)
    except Exception as exc:
        logger.exception("Assistant failed")
        await notifier.error("Сбой обработки вопроса", exc)
        await message.answer("Не удалось обработать вопрос. Передал администратору.")
        return
    if result.kind == AnswerKind.NO_FACT and result.ticket_id is not None:
        ticket = await tickets.get(result.ticket_id)
        if ticket is not None:
            await notifier.owner(
                question_card(ticket),
                markup=question_decision(ticket.id),
            )
    if result.kind == AnswerKind.ERROR:
        await notifier.error("LLM/поиск недоступен")
    await message.answer(result.text, parse_mode=None)


@router.message(F.text, ~F.text.startswith("/"), ~F.text.in_(RESERVED))
async def ai_answer(
    message: Message,
    assistant: AssistantService,
    notifier: Notifier,
    tickets: TicketService,
) -> None:
    if message.from_user is None:
        return
    question = (message.text or "").strip()
    if not question:
        return
    await _process(message, question, assistant, notifier, tickets)


@router.message(F.caption, ~F.caption.startswith("/"), ~F.caption.in_(RESERVED))
async def ai_caption(
    message: Message,
    assistant: AssistantService,
    notifier: Notifier,
    tickets: TicketService,
) -> None:
    if message.from_user is None:
        return
    question = (message.caption or "").strip()
    if not question:
        return
    await _process(message, question, assistant, notifier, tickets)
