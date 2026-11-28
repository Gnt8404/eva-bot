"""Owner-only admin menu, console, decisions and learning loop."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.bot.filters import IsOwner
from app.bot.keyboards import (
    BTN_CONSOLE,
    CB_ADMIN,
    CB_CONSOLE,
    admin_menu,
    application_decision,
    broadcast_confirm,
    confirm_console,
    console_commands,
    donation_decision,
    question_decision,
    report_decision,
    whitelist_decision,
)
from app.bot.states import BroadcastForm, ConsoleForm, FaqAnswer
from app.config import Settings
from app.db.models import Donation, User
from app.db.session import Database
from app.kb.learn import LearningService
from app.kb.store import KnowledgeBase
from app.notify import Notifier
from app.security.audit import AuditService
from app.server.commands import COMMANDS, ConsoleError, ConsoleService
from app.server.pterodactyl import PanelError, PterodactylClient
from app.server.status import StatusService
from app.tickets.cards import (
    application_card,
    decision_message,
    donation_card,
    donor_message,
    generic_card,
    question_card,
    report_card,
    whitelist_card,
)
from app.tickets.model import TicketKind, TicketStatus
from app.tickets.service import DonationService, TicketService
from app.utils import escape

logger = logging.getLogger(__name__)
router = Router(name="admin")


def _owner(settings: Settings, user_id: int | None) -> bool:
    return user_id is not None and settings.is_owner(user_id)


async def _deny(callback: CallbackQuery, settings: Settings, audit: AuditService) -> None:
    if callback.from_user is not None:
        await audit.record(
            actor_id=callback.from_user.id,
            action="admin_denied",
            target=(callback.data or "")[:100],
            result="denied",
        )
    await callback.answer("Доступ только у владельца.", show_alert=True)


@router.message(Command("admin"), IsOwner())
async def admin_command(message: Message) -> None:
    await message.answer(
        "Админ-меню Aetherlyn. Консоль, тикеты, база знаний, бэкапы и рассылка.",
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data == CB_ADMIN)
async def admin_open_callback(
    callback: CallbackQuery, settings: Settings, audit: AuditService
) -> None:
    if not _owner(settings, callback.from_user.id if callback.from_user else None):
        await _deny(callback, settings, audit)
        return
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Админ-меню Aetherlyn.", reply_markup=admin_menu())


@router.callback_query(F.data == "admin:stats")
async def admin_stats(
    callback: CallbackQuery,
    settings: Settings,
    audit: AuditService,
    kb: KnowledgeBase,
    tickets: TicketService,
    donations: DonationService,
    status: StatusService,
) -> None:
    if not _owner(settings, callback.from_user.id if callback.from_user else None):
        await _deny(callback, settings, audit)
        return
    stats = await kb.stats()
    totals = await donations.confirmed_totals()
    snapshot = await status.snapshot()
    text = (
        f"Посты: {stats['posts']}, страницы: {stats['pages']}, чанки: {stats['chunks']}.\n"
        f"Открытых тикетов: {await tickets.count_open()}.\n"
        f"Донаты за сутки: {totals['count']} на {totals['rub']:.2f} ₽ / {totals['usd']:.2f} $.\n"
        f"{snapshot.as_text()}"
    )
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(text)


@router.callback_query(F.data == "admin:tickets")
async def admin_tickets(
    callback: CallbackQuery,
    settings: Settings,
    audit: AuditService,
    tickets: TicketService,
    database: Database,
) -> None:
    if not _owner(settings, callback.from_user.id if callback.from_user else None):
        await _deny(callback, settings, audit)
        return
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    pending = await tickets.list_pending(5)
    if not pending:
        await callback.message.answer("Открытых тикетов нет.")
        return
    for ticket in pending:
        if ticket.kind == TicketKind.REPORT.value:
            card, mark = report_card(ticket), report_decision(ticket.id)
        elif ticket.kind == TicketKind.APPLICATION.value:
            card, mark = application_card(ticket), application_decision(ticket.id)
        elif ticket.kind == TicketKind.WHITELIST.value:
            card, mark = whitelist_card(ticket), whitelist_decision(ticket.id)
        elif ticket.kind == TicketKind.QUESTION.value:
            card, mark = question_card(ticket), question_decision(ticket.id)
        elif ticket.kind == TicketKind.DONATION.value:
            async with database.session() as session:
                donation = await session.scalar(
                    select(Donation).where(Donation.ticket_id == ticket.id)
                )
            if donation is None:
                card, mark = generic_card(ticket), None
            else:
                card = donation_card(donation, ticket_id=ticket.id)
                mark = donation_decision(donation.id)
        else:
            card, mark = generic_card(ticket), None
        await callback.message.answer(card, reply_markup=mark)


@router.callback_query(F.data == "admin:reindex")
async def admin_reindex(
    callback: CallbackQuery,
    settings: Settings,
    audit: AuditService,
    kb: KnowledgeBase,
) -> None:
    if not _owner(settings, callback.from_user.id if callback.from_user else None):
        await _deny(callback, settings, audit)
        return
    await callback.answer("Реиндекс запущен")
    if not isinstance(callback.message, Message):
        return
    try:
        stats = await kb.reindex()
        text = f"Реиндекс готов: страниц {stats.documents}, чанков {stats.chunks}, постов {stats.posts}."
    except Exception as exc:
        logger.exception("reindex failed")
        text = f"Реиндекс не удался: {type(exc).__name__}."
    await callback.message.answer(text)


@router.callback_query(F.data == "admin:log")
async def admin_log(
    callback: CallbackQuery,
    settings: Settings,
    audit: AuditService,
    status: StatusService,
) -> None:
    if not _owner(settings, callback.from_user.id if callback.from_user else None):
        await _deny(callback, settings, audit)
        return
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    ok, text = await status.latest_log(200)
    await callback.message.answer(text[-4000:] if ok else text)


@router.callback_query(F.data == "admin:backup")
async def admin_backup(
    callback: CallbackQuery,
    settings: Settings,
    audit: AuditService,
    panel: PterodactylClient | None,
) -> None:
    if not _owner(settings, callback.from_user.id if callback.from_user else None):
        await _deny(callback, settings, audit)
        return
    if panel is None or not panel.ready:
        await callback.answer("Панель не настроена.", show_alert=True)
        return
    await callback.answer()
    try:
        backups = await panel.list_backups()
    except PanelError as exc:
        if isinstance(callback.message, Message):
            await callback.message.answer(f"Бэкапы недоступны: {type(exc).__name__}.")
        return
    if not backups:
        text = "Бэкапов нет."
    else:
        text = "Последние бэкапы:\n" + "\n".join(
            f"• {item.get('name', 'backup')} — {item.get('bytes', 0)} байт" for item in backups[:10]
        )
    if isinstance(callback.message, Message):
        await callback.message.answer(text)


@router.callback_query(F.data == "admin:broadcast")
async def admin_broadcast_start(
    callback: CallbackQuery,
    settings: Settings,
    audit: AuditService,
    state: FSMContext,
) -> None:
    if not _owner(settings, callback.from_user.id if callback.from_user else None):
        await _deny(callback, settings, audit)
        return
    await state.set_state(BroadcastForm.text)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer("Введите текст рассылки. Он уйдёт всем пользователям.")


@router.message(BroadcastForm.text)
async def admin_broadcast_text(
    message: Message,
    state: FSMContext,
    settings: Settings,
    database: Database,
) -> None:
    if not _owner(settings, message.from_user.id if message.from_user else None):
        return
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("Слишком короткий текст.")
        return
    async with database.session() as session:
        count = len(
            (
                await session.scalars(
                    select(User).where(User.captcha_ok.is_(True), User.is_banned.is_(False))
                )
            ).all()
        )
    await state.set_state(BroadcastForm.confirm)
    await state.update_data(broadcast_text=text)
    await message.answer(
        f"Получателей: {count}. Подтвердить рассылку?",
        reply_markup=broadcast_confirm(count),
    )


@router.callback_query(F.data == "admin:broadcast:confirm")
async def admin_broadcast_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    database: Database,
    notifier: Notifier,
    audit: AuditService,
) -> None:
    if not _owner(settings, callback.from_user.id if callback.from_user else None):
        await _deny(callback, settings, audit)
        return
    data = await state.get_data()
    text = str(data.get("broadcast_text") or "").strip()
    await state.clear()
    if not text:
        await callback.answer("Текст потерян.", show_alert=True)
        return
    async with database.session() as session:
        user_ids = list(
            (
                await session.scalars(
                    select(User.id).where(User.captcha_ok.is_(True), User.is_banned.is_(False))
                )
            ).all()
        )
    await callback.answer("Рассылка начата")
    sent, failed = await notifier.broadcast(user_ids, escape(text))
    await audit.record(
        actor_id=callback.from_user.id if callback.from_user else 0,
        action="broadcast",
        target=f"users={len(user_ids)}",
        result="ok",
        details=f"sent={sent}, failed={failed}",
    )


@router.message(F.text == BTN_CONSOLE)
async def console_entry(message: Message, settings: Settings, audit: AuditService) -> None:
    if not settings.is_owner(message.from_user.id if message.from_user else 0):
        await audit.record(
            actor_id=message.from_user.id if message.from_user else 0,
            action="console_denied",
            target="button",
            result="denied",
        )
        await message.answer("Консоль доступна только владельцу.")
        return
    items = [(i, spec.title, spec.destructive) for i, spec in enumerate(COMMANDS)]
    await message.answer(
        "Консоль Aetherlyn. Каждая команда требует подтверждения и пишется в audit.",
        reply_markup=console_commands(items),
    )


@router.callback_query(F.data == CB_CONSOLE)
async def console_callback(
    callback: CallbackQuery, settings: Settings, audit: AuditService
) -> None:
    if not _owner(settings, callback.from_user.id if callback.from_user else None):
        await _deny(callback, settings, audit)
        return
    items = [(i, spec.title, spec.destructive) for i, spec in enumerate(COMMANDS)]
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Консоль Aetherlyn. Каждая команда требует подтверждения.",
            reply_markup=console_commands(items),
        )


async def _show_confirm(message: Message, command: str, token: str) -> None:
    await message.answer(
        f"Команда: <code>{escape(command)}</code>\nПодтвердите в течение 30 секунд.",
        parse_mode="HTML",
        reply_markup=confirm_console(token, command),
    )


@router.callback_query(F.data.startswith("console:cmd:"))
async def console_choose(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    audit: AuditService,
    console: ConsoleService,
) -> None:
    if not _owner(settings, callback.from_user.id if callback.from_user else None):
        await _deny(callback, settings, audit)
        return
    raw = (callback.data or "").split(":")[-1]
    if not raw.isdigit() or int(raw) >= len(COMMANDS):
        await callback.answer("Неизвестная команда", show_alert=True)
        return
    spec = COMMANDS[int(raw)]
    if spec.needs_text:
        await state.set_state(ConsoleForm.argument)
        await state.update_data(command_index=int(raw))
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.answer(f"Введите команду полностью, например: {spec.command}")
        return
    try:
        pending = await console.prepare(callback.from_user.id, spec.command)
    except ConsoleError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()
    if isinstance(callback.message, Message):
        await _show_confirm(callback.message, pending.command, pending.token)


@router.message(ConsoleForm.argument)
async def console_argument(
    message: Message,
    state: FSMContext,
    settings: Settings,
    console: ConsoleService,
) -> None:
    if not settings.is_owner(message.from_user.id if message.from_user else 0):
        return
    actor_id = message.from_user.id if message.from_user else 0
    data = await state.get_data()
    index = int(data.get("command_index") or -1)
    if index < 0 or index >= len(COMMANDS):
        await state.clear()
        await message.answer("Команда не найдена.")
        return
    try:
        pending = await console.prepare(actor_id, message.text or "")
    except ConsoleError as exc:
        await message.answer(f"Отклонено: {exc}")
        return
    await state.clear()
    await _show_confirm(message, pending.command, pending.token)


@router.callback_query(F.data.startswith("console:confirm:"))
async def console_confirm(
    callback: CallbackQuery,
    settings: Settings,
    audit: AuditService,
    console: ConsoleService,
) -> None:
    if not _owner(settings, callback.from_user.id if callback.from_user else None):
        await _deny(callback, settings, audit)
        return
    token = (callback.data or "").split(":", 2)[-1]
    try:
        output = await console.confirm(callback.from_user.id, token)
    except ConsoleError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Выполнено")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(f"Результат:\n{output}")


@router.callback_query(F.data.startswith("console:cancel:"))
async def console_cancel(
    callback: CallbackQuery,
    settings: Settings,
    console: ConsoleService,
) -> None:
    if not settings.is_owner(callback.from_user.id if callback.from_user else 0):
        await callback.answer("Доступ только у владельца.", show_alert=True)
        return
    token = (callback.data or "").split(":", 2)[-1]
    await console.cancel(callback.from_user.id, token)
    await callback.answer("Отменено")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("ticket:report:"))
async def report_decision_callback(
    callback: CallbackQuery,
    settings: Settings,
    audit: AuditService,
    tickets: TicketService,
    notifier: Notifier,
) -> None:
    if not _owner(settings, callback.from_user.id if callback.from_user else None):
        await _deny(callback, settings, audit)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or not parts[2].isdigit():
        await callback.answer("Некорректное действие", show_alert=True)
        return
    ticket_id, action = int(parts[2]), parts[3]
    status = {
        "approve": TicketStatus.APPROVED,
        "reject": TicketStatus.REJECTED,
        "details": TicketStatus.NEEDS_INFO,
    }.get(action)
    if status is None:
        await callback.answer("Некорректное действие", show_alert=True)
        return
    ticket = await tickets.get(ticket_id)
    if ticket is None:
        await callback.answer("Тикет не найден", show_alert=True)
        return
    await tickets.decide(ticket_id, status=status, decided_by=callback.from_user.id)
    await notifier.user(
        ticket.user_id, decision_message(TicketKind.REPORT.value, status.value, ticket_id)
    )
    await callback.answer("Решение сохранено")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("ticket:app:"))
async def application_decision_callback(
    callback: CallbackQuery,
    settings: Settings,
    audit: AuditService,
    tickets: TicketService,
    notifier: Notifier,
) -> None:
    if not _owner(settings, callback.from_user.id if callback.from_user else None):
        await _deny(callback, settings, audit)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or not parts[2].isdigit():
        await callback.answer("Некорректное действие", show_alert=True)
        return
    ticket_id, action = int(parts[2]), parts[3]
    status = {
        "probation": TicketStatus.PROBATION,
        "reject": TicketStatus.REJECTED,
        "reserve": TicketStatus.RESERVE,
    }.get(action)
    if status is None:
        await callback.answer("Некорректное действие", show_alert=True)
        return
    ticket = await tickets.get(ticket_id)
    if ticket is None:
        await callback.answer("Тикет не найден", show_alert=True)
        return
    await tickets.decide(ticket_id, status=status, decided_by=callback.from_user.id)
    await notifier.user(
        ticket.user_id, decision_message(TicketKind.APPLICATION.value, status.value, ticket_id)
    )
    await callback.answer("Решение сохранено")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("ticket:white:"))
async def whitelist_decision_callback(
    callback: CallbackQuery,
    settings: Settings,
    audit: AuditService,
    tickets: TicketService,
    notifier: Notifier,
) -> None:
    if not _owner(settings, callback.from_user.id if callback.from_user else None):
        await _deny(callback, settings, audit)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or not parts[2].isdigit():
        await callback.answer("Некорректное действие", show_alert=True)
        return
    ticket_id, action = int(parts[2]), parts[3]
    status = {
        "approve": TicketStatus.APPROVED,
        "reject": TicketStatus.REJECTED,
        "details": TicketStatus.NEEDS_INFO,
    }.get(action)
    if status is None:
        await callback.answer("Некорректное действие", show_alert=True)
        return
    ticket = await tickets.get(ticket_id)
    if ticket is None or ticket.kind != TicketKind.WHITELIST.value:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await tickets.decide(ticket_id, status=status, decided_by=callback.from_user.id)
    await notifier.user(
        ticket.user_id, decision_message(TicketKind.WHITELIST.value, status.value, ticket_id)
    )
    await callback.answer("Решение сохранено")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("ticket:don:"))
async def donation_decision_callback(
    callback: CallbackQuery,
    settings: Settings,
    audit: AuditService,
    donations: DonationService,
    console: ConsoleService,
    notifier: Notifier,
) -> None:
    if not _owner(settings, callback.from_user.id if callback.from_user else None):
        await _deny(callback, settings, audit)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or not parts[2].isdigit():
        await callback.answer("Некорректное действие", show_alert=True)
        return
    donation_id, action = int(parts[2]), parts[3]
    donation = await donations.get(donation_id)
    if donation is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if action == "confirm":
        result = await donations.confirm(
            donation_id, decided_by=callback.from_user.id, console=console
        )
        if result.ok and result.promo:
            await notifier.user(
                donation.user_id, donor_message(donation.package_label, result.promo)
            )
            await callback.answer("Промокод выдан")
        else:
            await notifier.owner(
                f"<b>Выдача не удалась</b>\nЗаявка #{donation_id}\n{escape(result.error or '')}"
            )
            await callback.answer("Ошибка выдачи, владелец уведомлён", show_alert=True)
        if isinstance(callback.message, Message):
            await callback.message.edit_reply_markup(reply_markup=None)
        return
    if action == "reject":
        await donations.reject(
            donation_id, decided_by=callback.from_user.id, note="rejected by owner"
        )
        await notifier.user(
            donation.user_id,
            f"Заявка на донат #{donation_id} отклонена. Проверьте чек и реквизиты.",
        )
    elif action == "receipt":
        await donations.ask_for_receipt(donation_id, decided_by=callback.from_user.id)
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Отправить новый чек",
                        callback_data=f"donate:newreceipt:{donation_id}",
                    )
                ]
            ]
        )
        await notifier.user(
            donation.user_id,
            "Владелец просит прислать чек ещё раз. Нажмите кнопку и отправьте фото или файл.",
            markup=markup,
        )
    else:
        await callback.answer("Некорректное действие", show_alert=True)
        return
    await callback.answer("Решение сохранено")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("faq:answer:"))
async def faq_answer_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    audit: AuditService,
    tickets: TicketService,
) -> None:
    if not _owner(settings, callback.from_user.id if callback.from_user else None):
        await _deny(callback, settings, audit)
        return
    raw = (callback.data or "").split(":")[-1]
    if not raw.isdigit():
        await callback.answer("Некорректный тикет", show_alert=True)
        return
    ticket = await tickets.get(int(raw))
    if ticket is None or ticket.kind != TicketKind.QUESTION.value:
        await callback.answer("Вопрос не найден", show_alert=True)
        return
    question = str((ticket.payload or {}).get("question") or ticket.subject)
    await state.set_state(FaqAnswer.waiting)
    await state.update_data(faq_ticket_id=ticket.id, faq_question=question)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Вопрос:\n{question}\n\nОтправьте краткий ответ (до 900 символов).",
            parse_mode=None,
        )


@router.message(FaqAnswer.waiting)
async def faq_answer_save(
    message: Message,
    state: FSMContext,
    settings: Settings,
    learning: LearningService,
    tickets: TicketService,
    notifier: Notifier,
) -> None:
    if not settings.is_owner(message.from_user.id if message.from_user else 0):
        return
    data = await state.get_data()
    ticket_id = int(data.get("faq_ticket_id") or 0)
    question = str(data.get("faq_question") or "").strip()
    answer = (message.text or "").strip()
    if not ticket_id or not question or len(answer) < 2:
        await message.answer("Ответ слишком короткий.")
        return
    await learning.apply_owner_answer(
        question=question,
        answer=answer[:900],
        owner_id=message.from_user.id,  # type: ignore[union-attr]
    )
    await tickets.decide(
        ticket_id,
        status=TicketStatus.ANSWERED,
        decided_by=message.from_user.id,  # type: ignore[union-attr]
    )
    await state.clear()
    ticket = await tickets.get(ticket_id)
    if ticket is not None:
        await notifier.user(
            ticket.user_id,
            f"{answer[:900]}\nИсточник: FAQ-оверрайд владельца.",
            parse_mode=None,
        )
    await message.answer("Ответ сохранён в FAQ-оверрайдах и сразу участвует в поиске.")
