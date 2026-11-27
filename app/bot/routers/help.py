"""Contextual help topics and escalation to the owner."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    BTN_HELP,
    CB_HELP_ADMIN,
    escalation_menu,
    help_menu,
    main_menu,
    question_decision,
)
from app.bot.states import HelpForm
from app.config import Settings
from app.llm.guard import sanitize_input
from app.llm.service import AnswerKind, AssistantService
from app.notify import Notifier
from app.tickets.cards import generic_card, question_card
from app.tickets.model import TicketKind
from app.tickets.service import TicketService

router = Router(name="help")

TOPICS = {
    "login": "Как зайти на сервер Aetherlyn, регистрация /register и вход /login",
    "lag": "Лаги и производительность на сервере Aetherlyn, TPS и MSPT",
    "lost": "Потеря вещей после смерти на Aetherlyn, могилы AxGraves",
    "grief": "Гриферство на Aetherlyn, CoreProtect и откат блоков",
    "sorrow": "Мир Sorrow на Aetherlyn: записки, квесты, промокоды, мимик, рулетка",
    "donate": "Донат на Aetherlyn: печати, цены, оплата и выдача промокода",
    "other": "Частые вопросы о сервере Aetherlyn",
}


async def _topic_answer(
    callback: CallbackQuery,
    topic: str,
    assistant: AssistantService,
    tickets: TicketService,
    notifier: Notifier,
) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    await callback.answer()
    query = TOPICS.get(topic, TOPICS["other"])
    result = await assistant.answer(callback.from_user.id, query)
    if result.kind == AnswerKind.NO_FACT and result.ticket_id is not None:
        ticket = await tickets.get(result.ticket_id)
        if ticket is not None:
            await notifier.owner(question_card(ticket), markup=question_decision(ticket.id))
    if isinstance(callback.message, Message):
        await callback.message.answer(
            result.text, reply_markup=escalation_menu(topic), parse_mode=None
        )


@router.message(F.text == BTN_HELP)
async def help_entry(message: Message) -> None:
    await message.answer(
        "Выберите тему — ответ будет только по фактам базы знаний.", reply_markup=help_menu()
    )


@router.message(Command("back"))
async def back_command(message: Message) -> None:
    await message.answer(
        "В игре /back возвращает к месту смерти, если команда включена. "
        "Если вещи пропали из-за ошибки, откройте «Нужна помощь» → «Потеря вещей» "
        "и создайте обращение. Источник: #Aetherlyn",
        parse_mode=None,
    )


@router.callback_query(F.data.startswith("help:topic:"))
async def help_topic(
    callback: CallbackQuery,
    assistant: AssistantService,
    tickets: TicketService,
    notifier: Notifier,
) -> None:
    await _topic_answer(
        callback,
        (callback.data or "").split(":")[-1],
        assistant,
        tickets,
        notifier,
    )


@router.callback_query(F.data.startswith("help:escalate:"))
async def escalate(callback: CallbackQuery, state: FSMContext) -> None:
    topic = (callback.data or "").split(":")[-1]
    await state.set_state(HelpForm.admin_prompt)
    await state.update_data(help_topic=topic)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Опишите проблему одним сообщением. Администратор получит тикет."
        )


@router.callback_query(F.data == CB_HELP_ADMIN)
async def escalate_admin(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(HelpForm.admin_prompt)
    await state.update_data(help_topic="other")
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer("Опишите проблему одним сообщением.")


@router.message(HelpForm.admin_prompt)
async def escalation_text(
    message: Message,
    state: FSMContext,
    tickets: TicketService,
    notifier: Notifier,
    settings: Settings,
) -> None:
    data = await state.get_data()
    topic = str(data.get("help_topic") or "other")
    text = sanitize_input(message.text or "", 1000)
    if len(text) < 5:
        await message.answer("Слишком коротко. Опишите проблему подробнее.")
        return
    await state.clear()
    assert message.from_user is not None
    ticket = await tickets.create(
        kind=TicketKind.SUPPORT,
        user_id=message.from_user.id,
        subject=f"Помощь: {topic}",
        payload={"topic": topic, "message": text},
    )
    await notifier.owner(generic_card(ticket))
    await message.answer(
        f"Тикет #{ticket.id} создан. Администратор ответит в этом чате.",
        reply_markup=main_menu(settings.is_owner(message.from_user.id)),
    )
