"""Step-by-step player report with media evidence."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    BTN_REPORT,
    CB_REPORT_START,
    cancel_menu,
    main_menu,
    report_confirm_menu,
    report_decision,
)
from app.bot.media import (
    FormError,
    date_value,
    nickname,
    text_value,
    validate_report_evidence,
)
from app.bot.states import ReportForm
from app.config import Settings
from app.notify import Notifier
from app.tickets.cards import report_card
from app.tickets.model import TicketKind
from app.tickets.service import TicketService

router = Router(name="report")


async def _begin(message: Message, state: FSMContext) -> None:
    await state.set_state(ReportForm.offender)
    await message.answer(
        "Шаг 1/5. Ник нарушителя (точно как в игре).",
        reply_markup=cancel_menu(),
    )


@router.message(F.text == BTN_REPORT)
async def report_entry(message: Message, state: FSMContext) -> None:
    await _begin(message, state)


@router.callback_query(F.data == CB_REPORT_START)
async def report_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await _begin(callback.message, state)


@router.message(ReportForm.offender)
async def step_offender(message: Message, state: FSMContext) -> None:
    try:
        value = nickname(message.text or "")
    except FormError as exc:
        await message.answer(f"{exc}. Попробуйте ещё раз.")
        return
    await state.update_data(offender=value)
    await state.set_state(ReportForm.reporter)
    await message.answer("Шаг 2/5. Ваш ник в игре.", reply_markup=cancel_menu())


@router.message(ReportForm.reporter)
async def step_reporter(message: Message, state: FSMContext) -> None:
    try:
        value = nickname(message.text or "")
    except FormError as exc:
        await message.answer(f"{exc}. Попробуйте ещё раз.")
        return
    await state.update_data(reporter=value)
    await state.set_state(ReportForm.happened_at)
    await message.answer(
        "Шаг 3/5. Дата и время происшествия (МСК), например 23.11.2026 21:30.",
        reply_markup=cancel_menu(),
    )


@router.message(ReportForm.happened_at)
async def step_when(message: Message, state: FSMContext) -> None:
    try:
        value = date_value(message.text or "")
    except FormError as exc:
        await message.answer(f"{exc}. Попробуйте ещё раз.")
        return
    await state.update_data(happened_at=value)
    await state.set_state(ReportForm.description)
    await message.answer("Шаг 4/5. Что произошло? До 500 символов.", reply_markup=cancel_menu())


@router.message(ReportForm.description)
async def step_description(message: Message, state: FSMContext) -> None:
    try:
        value = text_value(message.text or "", field="Описание", minimum=10, maximum=500)
    except FormError as exc:
        await message.answer(f"{exc}. Попробуйте ещё раз.")
        return
    await state.update_data(description=value)
    await state.set_state(ReportForm.evidence)
    await message.answer(
        "Шаг 5/5. Пришлите скриншот или короткое видео (до 20 МБ).",
        reply_markup=cancel_menu(),
    )


@router.message(ReportForm.evidence, F.photo | F.document | F.video)
async def step_evidence(
    message: Message,
    state: FSMContext,
    bot: Bot,
    settings: Settings,
) -> None:
    try:
        media = await validate_report_evidence(message, bot, settings)
    except FormError as exc:
        await message.answer(f"{exc}. Пришлите файл ещё раз.")
        return
    await state.update_data(
        evidence_file_id=media.file_id,
        evidence_kind=media.kind,
    )
    data = await state.get_data()
    await state.set_state(ReportForm.confirm)
    summary = (
        "Проверьте жалобу:\n"
        f"Нарушитель: {data['offender']}\n"
        f"Ваш ник: {data['reporter']}\n"
        f"Когда: {data['happened_at']}\n"
        f"Описание: {data['description']}\n"
        "Доказательство приложено."
    )
    await message.answer(summary, reply_markup=report_confirm_menu())


@router.message(ReportForm.evidence)
async def step_evidence_wrong(message: Message) -> None:
    await message.answer("Нужен скриншот (JPEG/PNG/WEBP) или видео до 20 МБ.")


@router.callback_query(F.data == "report:confirm")
async def confirm_report(
    callback: CallbackQuery,
    state: FSMContext,
    tickets: TicketService,
    notifier: Notifier,
    settings: Settings,
) -> None:
    data = await state.get_data()
    if not data.get("offender") or not data.get("description"):
        await callback.answer("Форма устарела. Начните заново.", show_alert=True)
        await state.clear()
        return
    await state.clear()
    if callback.from_user is None:
        await callback.answer()
        return
    ticket = await tickets.create(
        kind=TicketKind.REPORT,
        user_id=callback.from_user.id,
        subject=f"Жалоба на {data['offender']}",
        payload={
            "offender": data.get("offender"),
            "reporter": data.get("reporter"),
            "happened_at": data.get("happened_at"),
            "description": data.get("description"),
            "evidence_file_id": data.get("evidence_file_id"),
            "screenshot_file_id": data.get("evidence_file_id"),
            "evidence_kind": data.get("evidence_kind"),
            "username": callback.from_user.username or callback.from_user.full_name,
        },
    )
    card = report_card(ticket)
    markup = report_decision(ticket.id)
    kind = str(data.get("evidence_kind") or "photo")
    file_id = str(data.get("evidence_file_id") or "")
    if file_id:
        await notifier.owner_attachment(card, file_id=file_id, kind=kind, markup=markup)
    else:
        await notifier.owner(card, markup=markup)
    await notifier.team(card)
    await callback.answer("Жалоба отправлена")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"Жалоба #{ticket.id} принята. Решение придёт в этот чат.",
            reply_markup=main_menu(settings.is_owner(callback.from_user.id)),
        )
