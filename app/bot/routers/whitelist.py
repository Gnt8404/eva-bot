"""Whitelist application form, separate from the team application."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    BTN_WHITELIST,
    CB_WHITELIST_START,
    main_menu,
    whitelist_confirm_menu,
    whitelist_decision,
    whitelist_platform_menu,
    whitelist_rules_menu,
)
from app.bot.media import FormError, age_value, nickname, text_value
from app.bot.states import WhitelistForm
from app.config import Settings
from app.notify import Notifier
from app.tickets.cards import whitelist_card
from app.tickets.model import TicketKind
from app.tickets.service import TicketService

router = Router(name="whitelist")

INTRO = (
    "Заявка в белый список Aetherlyn.\n"
    "Это отдельная заявка на доступ к серверу, не анкета в команду.\n"
    "Шаг 1/6. Ваш ник в игре."
)


async def _begin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(WhitelistForm.nickname)
    await message.answer(INTRO)


@router.message(Command("whitelist"))
@router.message(F.text == BTN_WHITELIST)
async def whitelist_entry(message: Message, state: FSMContext) -> None:
    await _begin(message, state)


@router.callback_query(F.data == CB_WHITELIST_START)
async def whitelist_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await _begin(callback.message, state)


@router.message(WhitelistForm.nickname)
async def step_nickname(message: Message, state: FSMContext) -> None:
    try:
        value = nickname(message.text or "")
    except FormError as exc:
        await message.answer(f"{exc}. Попробуйте ещё раз.")
        return
    await state.update_data(nickname=value)
    await state.set_state(WhitelistForm.platform)
    await message.answer("Шаг 2/6. Ваша платформа?", reply_markup=whitelist_platform_menu())


@router.callback_query(WhitelistForm.platform, F.data.startswith("whitelist:platform:"))
async def step_platform(callback: CallbackQuery, state: FSMContext) -> None:
    raw = (callback.data or "").rsplit(":", 1)[-1]
    if raw not in {"java", "bedrock"}:
        await callback.answer("Неизвестная платформа", show_alert=True)
        return
    value = "Java" if raw == "java" else "Bedrock"
    await state.update_data(platform=value)
    await state.set_state(WhitelistForm.age)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Шаг 3/6. Сколько вам полных лет?")


@router.message(WhitelistForm.age)
async def step_age(message: Message, state: FSMContext) -> None:
    try:
        value = age_value(message.text or "")
    except FormError as exc:
        await message.answer(f"{exc}. Попробуйте ещё раз.")
        return
    await state.update_data(age=value)
    await state.set_state(WhitelistForm.experience)
    await message.answer(
        "Шаг 4/6. Опыт игры на серверах: что играли, сколько времени. "
        "Если опыта нет, отправьте «-»."
    )


@router.message(WhitelistForm.experience)
async def step_experience(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        value = "нет" if raw == "-" else text_value(raw, field="Опыт", minimum=5, maximum=400)
    except FormError as exc:
        await message.answer(f"{exc}. Попробуйте ещё раз или отправьте «-».")
        return
    await state.update_data(experience=value)
    await state.set_state(WhitelistForm.motivation)
    await message.answer("Шаг 5/6. Почему хотите играть именно на Aetherlyn? До 500 символов.")


@router.message(WhitelistForm.motivation)
async def step_motivation(message: Message, state: FSMContext) -> None:
    try:
        value = text_value(message.text or "", field="Ответ", minimum=10, maximum=500)
    except FormError as exc:
        await message.answer(f"{exc}. Попробуйте ещё раз.")
        return
    await state.update_data(motivation=value)
    await state.set_state(WhitelistForm.rules)
    await message.answer(
        "Шаг 6/6. Ознакомьтесь с правилами Aetherlyn. "
        "Читы, гриферство и обман запрещены. Подтвердите, что принимаете правила.",
        reply_markup=whitelist_rules_menu(),
    )


@router.callback_query(WhitelistForm.rules, F.data == "whitelist:rules:yes")
async def step_rules(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(rules_accepted=True)
    await state.set_state(WhitelistForm.confirm)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        summary = (
            "Проверьте заявку в белый список:\n"
            f"Ник: {data['nickname']}\n"
            f"Платформа: {data['platform']}\n"
            f"Возраст: {data['age']}\n"
            f"Опыт: {data['experience']}\n"
            f"Почему Aetherlyn: {data['motivation']}\n"
            "Правила: приняты"
        )
        await callback.message.answer(summary, reply_markup=whitelist_confirm_menu())


@router.callback_query(F.data == "whitelist:confirm")
async def confirm_whitelist(
    callback: CallbackQuery,
    state: FSMContext,
    tickets: TicketService,
    notifier: Notifier,
    settings: Settings,
) -> None:
    data = await state.get_data()
    if not data.get("nickname") or not data.get("rules_accepted"):
        await callback.answer("Заявка устарела. Начните заново.", show_alert=True)
        await state.clear()
        return
    await state.clear()
    if callback.from_user is None:
        await callback.answer()
        return
    ticket = await tickets.create(
        kind=TicketKind.WHITELIST,
        user_id=callback.from_user.id,
        subject=f"Белый список: {data['nickname']}",
        payload={
            "nickname": data.get("nickname"),
            "platform": data.get("platform"),
            "age": data.get("age"),
            "experience": data.get("experience"),
            "motivation": data.get("motivation"),
            "username": callback.from_user.username or callback.from_user.full_name,
            "rules_accepted": True,
        },
    )
    card = whitelist_card(ticket)
    await notifier.owner(card, markup=whitelist_decision(ticket.id))
    await notifier.team(card)
    await callback.answer("Заявка отправлена")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"Заявка в белый список #{ticket.id} отправлена. Ответ придёт в этот чат.",
            reply_markup=main_menu(settings.is_owner(callback.from_user.id)),
        )
