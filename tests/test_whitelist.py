"""Whitelist application, menu separation and server-info regression tests."""

from __future__ import annotations

from datetime import UTC, datetime

from app.bot.keyboards import (
    BTN_APPLY,
    BTN_WHITELIST,
    main_menu,
    whitelist_decision,
)
from app.bot.routers.menu import STATUS_UNAVAILABLE, _server_text
from app.config import Settings
from app.db.models import Ticket
from app.tickets.cards import decision_message, whitelist_card
from app.tickets.model import TicketKind
from app.tickets.service import TicketService


def test_main_menu_separates_team_and_whitelist() -> None:
    labels = [button.text for row in main_menu(False).keyboard for button in row]
    assert BTN_APPLY == "Анкета в команду"
    assert BTN_WHITELIST == "Заявка в белый список"
    assert BTN_APPLY in labels
    assert BTN_WHITELIST in labels
    assert "Анкета" not in labels


def test_whitelist_card_is_labelled_and_escaped() -> None:
    ticket = Ticket(
        id=77,
        kind=TicketKind.WHITELIST.value,
        user_id=999,
        subject="Белый список: Steve",
        status="pending",
        payload={
            "nickname": "Steve",
            "platform": "Java",
            "age": "16",
            "experience": "2 года",
            "motivation": "Хочу играть <с друзьями>",
            "username": "steve",
            "rules_accepted": True,
        },
        created_at=datetime.now(UTC),
    )
    card = whitelist_card(ticket)
    assert "Заявка в белый список #77" in card
    assert "Платформа: Java" in card
    assert "&lt;с друзьями&gt;" in card
    assert "Правила приняты: да" in card


def test_whitelist_decision_callbacks() -> None:
    buttons = [
        button.callback_data for row in whitelist_decision(77).inline_keyboard for button in row
    ]
    assert "ticket:white:77:approve" in buttons
    assert "ticket:white:77:reject" in buttons
    assert "ticket:white:77:details" in buttons


def test_whitelist_decision_message_has_its_own_label() -> None:
    text = decision_message(TicketKind.WHITELIST.value, "approved", 77)
    assert "Заявка в белый список #77" in text
    assert "принята" in text


def test_server_text_always_has_fallback_and_source(settings: Settings) -> None:
    text = _server_text(settings, STATUS_UNAVAILABLE, [])
    assert "Aetherlyn" in text
    assert "Статус сейчас недоступен" in text
    assert "Источник:" in text


async def test_whitelist_ticket_is_distinct(tickets: TicketService) -> None:
    ticket = await tickets.create(
        kind=TicketKind.WHITELIST,
        user_id=999,
        subject="Белый список: Steve",
        payload={"nickname": "Steve", "rules_accepted": True},
    )
    assert ticket.kind == "whitelist"
    assert ticket.status == "pending"
