"""Team application form."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    BTN_APPLY,
    CB_APPLY_START,
    application_decision,
    apply_confirm_menu,
    apply_mic_menu,
    cancel_menu,
    main_menu,
)
from app.bot.media import (
    FormError,
    age_value,
    hours_value,
    nickname,
    portfolio_value,
    text_value,
)
from app.bot.states import ApplicationForm
from app.config import Settings
from app.notify import Notifier
from app.tickets.cards import application_card
from app.tickets.model import TicketKind
from app.tickets.service import TicketService

router = Router(name="apply")


async def _begin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ApplicationForm.age)
    await message.answer(
        "Анкета в команду Aetherlyn. Это заявка на модерацию/строительство, "
        "не белый список.\nШаг 1/7. Сколько вам полных лет?",
        reply_markup=cancel_menu(),
    )


@router.message(Command("apply"))
@router.message(F.text.in_({BTN_APPLY, "Анкета"}))
async def apply_entry(message: Message, state: FSMContext) -> None:
    await _begin(message, state)


@router.callback_query(F.data == CB_APPLY_START)
async def apply_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await _begin(callback.message, state)


@router.message(ApplicationForm.age)
async def step_age(message: Message, state: FSMContext) -> None:
    try:
        value = age_value(message.text or "")
    except FormError as exc:
        await message.answer(f"{exc}. Попробуйте ещё раз.")
        return
    await state.update_data(age=value)
    await state.set_state(ApplicationForm.nickname)
    await message.answer("Шаг 2/7. Ваш ник в игре.", reply_markup=cancel_menu())


@router.message(ApplicationForm.nickname)
async def step_nickname(message: Message, state: FSMContext) -> None:
    try:
        value = nickname(message.text or "")
    except FormError as exc:
        await message.answer(f"{exc}. Попробуйте ещё раз.")
        return
    await state.update_data(nickname=value)
    await state.set_state(ApplicationForm.experience)
    await message.answer(
        "Шаг 3/7. Опыт: что строили, где админили. До 500 символов.",
        reply_markup=cancel_menu(),
    )


@router.message(ApplicationForm.experience)
async def step_experience(message: Message, state: FSMContext) -> None:
    try:
        value = text_value(message.text or "", field="Опыт", minimum=10, maximum=500)
    except FormError as exc:
        await message.answer(f"{exc}. Попробуйте ещё раз.")
        return
    await state.update_data(experience=value)
    await state.set_state(ApplicationForm.hours_week)
    await message.answer(
        "Шаг 4/7. Сколько часов в неделю готовы уделять серверу?", reply_markup=cancel_menu()
    )


@router.message(ApplicationForm.hours_week)
async def step_hours(message: Message, state: FSMContext) -> None:
    try:
        value = hours_value(message.text or "")
    except FormError as exc:
        await message.answer(f"{exc}. Попробуйте ещё раз.")
        return
    await state.update_data(hours_week=value)
    await state.set_state(ApplicationForm.timezone)
    await message.answer("Шаг 5/7. Ваш часовой пояс (например, МСК+2).", reply_markup=cancel_menu())


@router.message(ApplicationForm.timezone)
async def step_timezone(message: Message, state: FSMContext) -> None:
    try:
        value = text_value(message.text or "", field="Часовой пояс", minimum=2, maximum=64)
    except FormError as exc:
        await message.answer(f"{exc}. Попробуйте ещё раз.")
        return
    await state.update_data(timezone=value)
    await state.set_state(ApplicationForm.microphone)
    await message.answer("Шаг 6/7. Есть рабочий микрофон?", reply_markup=apply_mic_menu())


@router.callback_query(ApplicationForm.microphone, F.data.startswith("apply:mic:"))
async def step_mic(callback: CallbackQuery, state: FSMContext) -> None:
    value = "да" if (callback.data or "").endswith("yes") else "нет"
    await state.update_data(microphone=value)
    await state.set_state(ApplicationForm.portfolio)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Шаг 7/7. Ссылка на портфолио или скриншоты. Если нет — отправьте «-».",
            reply_markup=cancel_menu(),
        )


@router.message(ApplicationForm.portfolio)
async def step_portfolio(message: Message, state: FSMContext) -> None:
    try:
        value = portfolio_value(message.text or "")
    except FormError as exc:
        await message.answer(f"{exc}. Попробуйте снова или отправьте «-».")
        return
    await state.update_data(portfolio=value)
    data = await state.get_data()
    await state.set_state(ApplicationForm.confirm)
    summary = (
        "Проверьте анкету:\n"
        f"Возраст: {data['age']}\n"
        f"Ник: {data['nickname']}\n"
        f"Опыт: {data['experience']}\n"
        f"Часов в неделю: {data['hours_week']}\n"
        f"Часовой пояс: {data['timezone']}\n"
        f"Микрофон: {data['microphone']}\n"
        f"Портфолио: {data['portfolio']}"
    )
    await message.answer(summary, reply_markup=apply_confirm_menu())


@router.callback_query(F.data == "apply:confirm")
async def confirm_application(
    callback: CallbackQuery,
    state: FSMContext,
    tickets: TicketService,
    notifier: Notifier,
    settings: Settings,
) -> None:
    data = await state.get_data()
    if not data.get("nickname") or not data.get("experience"):
        await callback.answer("Анкета устарела. Начните заново.", show_alert=True)
        await state.clear()
        return
    await state.clear()
    if callback.from_user is None:
        await callback.answer()
        return
    ticket = await tickets.create(
        kind=TicketKind.APPLICATION,
        user_id=callback.from_user.id,
        subject=f"Анкета в команду: {data['nickname']}",
        payload={
            "age": data.get("age"),
            "nickname": data.get("nickname"),
            "username": callback.from_user.username or callback.from_user.full_name,
            "experience": data.get("experience"),
            "hours_week": data.get("hours_week"),
            "timezone": data.get("timezone"),
            "microphone": data.get("microphone"),
            "portfolio": data.get("portfolio"),
        },
    )
    card = application_card(ticket)
    await notifier.owner(card, markup=application_decision(ticket.id))
    await notifier.team(card)
    await callback.answer("Анкета отправлена")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"Анкета #{ticket.id} отправлена. Ответ придёт в этот чат.",
            reply_markup=main_menu(settings.is_owner(callback.from_user.id)),
        )
