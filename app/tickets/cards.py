"""HTML cards sent to the owner and to the team chat.

Card builders never receive payment card numbers, tokens or other secrets.
"""

from __future__ import annotations

from app.db.models import Donation as DonationRow
from app.db.models import Ticket as TicketRow
from app.tickets.model import TicketKind, TicketStatus
from app.utils import display_user, escape, fmt_dt, shorten

KIND_TITLES = {
    TicketKind.REPORT: "Жалоба на игрока",
    TicketKind.APPLICATION: "Анкета в команду",
    TicketKind.WHITELIST: "Заявка в белый список",
    TicketKind.DONATION: "Заявка на донат",
    TicketKind.QUESTION: "Вопрос без ответа",
    TicketKind.SUPPORT: "Обращение",
}

UNKNOWN = "—"


def _value(payload: dict[str, object], key: str, limit: int = 500) -> str:
    value = payload.get(key)
    if value is None or str(value).strip() == "":
        return UNKNOWN
    return escape(shorten(str(value), limit))


def _header(ticket: TicketRow) -> str:
    title = KIND_TITLES.get(TicketKind(ticket.kind), "Обращение")
    return f"<b>{escape(title)} #{ticket.id}</b>"


def report_card(ticket: TicketRow) -> str:
    payload = ticket.payload or {}
    lines = [
        _header(ticket),
        f"Нарушитель: {_value(payload, 'offender', 64)}",
        f"Заявитель: {_value(payload, 'reporter', 64)}",
        f"Telegram: {escape(display_user(ticket.payload.get('username') if ticket.payload else None, ticket.user_id))}",
        f"Когда: {_value(payload, 'happened_at', 80)}",
        "Что произошло:",
        _value(payload, "description", 500),
        f"Доказательство: {'приложено' if payload.get('screenshot_file_id') else 'нет'}",
        f"Создано: {fmt_dt(ticket.created_at)}",
    ]
    return "\n".join(lines)


def application_card(ticket: TicketRow) -> str:
    payload = ticket.payload or {}
    lines = [
        _header(ticket),
        f"Возраст: {_value(payload, 'age', 16)}",
        f"Ник: {_value(payload, 'nickname', 32)}",
        f"Telegram: {_value(payload, 'username', 64)}",
        f"Опыт: {_value(payload, 'experience', 500)}",
        f"Часов в неделю: {_value(payload, 'hours_week', 32)}",
        f"Часовой пояс: {_value(payload, 'timezone', 64)}",
        f"Микрофон: {_value(payload, 'microphone', 16)}",
        f"Портфолио: {_value(payload, 'portfolio', 300)}",
        f"Создано: {fmt_dt(ticket.created_at)}",
    ]
    return "\n".join(lines)


def whitelist_card(ticket: TicketRow) -> str:
    payload = ticket.payload or {}
    lines = [
        _header(ticket),
        f"Ник в игре: {_value(payload, 'nickname', 32)}",
        f"Платформа: {_value(payload, 'platform', 16)}",
        f"Возраст: {_value(payload, 'age', 16)}",
        f"Опыт: {_value(payload, 'experience', 400)}",
        "Почему хочет на сервер:",
        _value(payload, "motivation", 500),
        f"Telegram: {_value(payload, 'username', 64)}",
        f"Правила приняты: {'да' if payload.get('rules_accepted') else 'нет'}",
        f"Создано: {fmt_dt(ticket.created_at)}",
    ]
    return "\n".join(lines)


def donation_card(donation: DonationRow, *, ticket_id: int | None = None) -> str:
    kind = "игровой пасс" if donation.package_id == "game-pass" else "печати"
    source = f"#{donation.id}" if ticket_id is None else f"#{donation.id} / тикет #{ticket_id}"
    lines = [
        f"<b>#Aetherlyn #donate</b>\n<b>Новая покупка</b> {escape(source)}",
        f"Ник: {escape(donation.nickname)}",
        f"Telegram: {escape(donation.tg_username or '—')} (id {donation.user_id})",
        f"Пакет: {escape(donation.package_label)} ({kind})",
        f"Сумма: {donation.amount_rub} ₽ / {donation.amount_usd:.2f} $ / {donation.amount_uzs:,} сум".replace(
            ",", " "
        ),
        f"Скриншот: {'приложен' if donation.screenshot_file_id else 'нет'}",
        "Проверьте чек вручную. Автоматического подтверждения нет.",
    ]
    return "\n".join(lines)


def question_card(ticket: TicketRow) -> str:
    payload = ticket.payload or {}
    lines = [
        f"<b>Вопрос без ответа #{ticket.id}</b>",
        f"От: {escape(display_user(ticket.payload.get('username') if ticket.payload else None, ticket.user_id))}",
        "Вопрос:",
        _value(payload, "question", 800),
        f"Создано: {fmt_dt(ticket.created_at)}",
        "Нажмите «Ответить», чтобы добавить факт в базу знаний.",
    ]
    return "\n".join(lines)


def generic_card(ticket: TicketRow) -> str:
    payload = ticket.payload or {}
    lines = [
        _header(ticket),
        f"Пользователь: id {ticket.user_id}",
        f"Тема: {escape(ticket.subject or UNKNOWN)}",
        f"Создано: {fmt_dt(ticket.created_at)}",
    ]
    for key, value in list(payload.items())[:8]:
        lines.append(f"{escape(key)}: {escape(shorten(str(value), 300))}")
    return "\n".join(lines)


def ticket_card(ticket: TicketRow) -> str:
    if ticket.kind == TicketKind.REPORT:
        return report_card(ticket)
    if ticket.kind == TicketKind.APPLICATION:
        return application_card(ticket)
    if ticket.kind == TicketKind.WHITELIST:
        return whitelist_card(ticket)
    if ticket.kind == TicketKind.QUESTION:
        return question_card(ticket)
    return generic_card(ticket)


def donor_message(package_label: str, promo: str) -> str:
    return (
        f"<b>Оплата подтверждена</b>\n"
        f"Пакет: {escape(package_label)}\n"
        f"Промокод: <code>{escape(promo)}</code>\n"
        "Введите его в игре командой из инструкции сервера. "
        "Если промокод не сработал, ответьте на это сообщение."
    )


def decision_message(kind: str, decision: str, ticket_id: int) -> str:
    if decision == TicketStatus.APPROVED.value:
        text = "принята"
    elif decision == TicketStatus.REJECTED.value:
        text = "отклонена"
    elif decision == TicketStatus.PROBATION.value:
        text = "принята на испытательный срок"
    elif decision == TicketStatus.RESERVE.value:
        text = "добавлена в резерв"
    elif decision == TicketStatus.NEEDS_INFO.value:
        text = "возвращена на уточнение"
    elif decision == TicketStatus.ANSWERED.value:
        text = "обработана"
    else:
        text = "обновлена"
    labels = {
        TicketKind.REPORT: "Жалоба",
        TicketKind.APPLICATION: "Анкета в команду",
        TicketKind.WHITELIST: "Заявка в белый список",
    }
    label = labels.get(TicketKind(kind), "Обращение")
    return f"{label} #{ticket_id} {text}. Решение администратора окончательное."
