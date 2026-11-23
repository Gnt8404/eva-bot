"""Owner boundary, console confirmation and ticket-access tests."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.config import Settings
from app.db.models import Audit, Ticket
from app.db.session import Database
from app.security.access import (
    PermissionDenied,
    can_broadcast,
    can_use_console,
    can_view_ticket,
)
from app.security.audit import AuditService
from app.server.commands import (
    ConfirmationExpired,
    ConsoleDenied,
    ConsoleService,
    validate_command,
)
from app.tickets.model import TicketKind, TicketStatus
from app.tickets.service import TicketService


class FakeRcon:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def command(self, command: str) -> str:
        self.calls.append(command)
        return "There are 3 of a max of 20 players online"


def test_owner_only_permissions(settings: Settings) -> None:
    owner = settings.owner_id
    stranger = 111111
    foreign_ticket = Ticket(id=1, kind="report", user_id=999, status="pending")
    own_ticket = Ticket(id=2, kind="report", user_id=stranger, status="pending")
    assert can_use_console(settings, owner)
    assert not can_use_console(settings, stranger)
    assert can_broadcast(settings, owner)
    assert not can_broadcast(settings, stranger)
    assert can_view_ticket(settings, foreign_ticket, owner)
    assert not can_view_ticket(settings, foreign_ticket, stranger)
    assert can_view_ticket(settings, own_ticket, stranger)


def test_console_denies_non_owner(settings: Settings) -> None:
    service = ConsoleService(settings, rcon=FakeRcon())
    with pytest.raises(PermissionDenied):
        asyncio.run(service.prepare(111111, "list"))


async def test_console_denial_is_audited(settings: Settings, database: Database) -> None:
    service = ConsoleService(settings, AuditService(database), rcon=FakeRcon())
    with pytest.raises(PermissionDenied):
        await service.prepare(111111, "list")
    async with database.session() as session:
        rows = (await session.scalars(select(Audit).where(Audit.action == "console_denied"))).all()
    assert rows
    assert rows[-1].actor_id == 111111


def test_console_denies_destructive_or_shell_commands(settings: Settings) -> None:
    for command in ("op Nick", "stop; rm -rf /", "give Nick diamond 64", "say hi; stop"):
        with pytest.raises(ConsoleDenied):
            validate_command(command)


def test_console_requires_two_steps_and_executes(settings: Settings) -> None:
    settings.rcon_password = SecretStr("test-rcon")
    rcon = FakeRcon()
    service = ConsoleService(settings, rcon=rcon)
    pending = asyncio.run(service.prepare(settings.owner_id, "list"))
    assert pending.command == "list"
    assert rcon.calls == []
    output = asyncio.run(service.confirm(settings.owner_id, pending.token))
    assert "3 of a max of 20" in output
    assert rcon.calls == ["list"]


def test_console_expired_token_is_rejected(settings: Settings) -> None:
    settings.rcon_password = SecretStr("test-rcon")
    service = ConsoleService(settings, rcon=FakeRcon())
    pending = asyncio.run(service.prepare(settings.owner_id, "version"))
    pending.expires_at = 0
    with pytest.raises(ConfirmationExpired):
        asyncio.run(service.confirm(settings.owner_id, pending.token))


async def test_ticket_decision_is_owner_only(settings: Settings, tickets: TicketService) -> None:
    ticket = await tickets.create(
        kind=TicketKind.REPORT,
        user_id=555,
        subject="grief",
        payload={"offender": "Nick"},
    )
    with pytest.raises(PermissionDenied):
        await tickets.decide(
            ticket.id,
            status=TicketStatus.APPROVED,
            decided_by=777,
        )
    decided = await tickets.decide(
        ticket.id,
        status=TicketStatus.APPROVED,
        decided_by=settings.owner_id,
        note="checked",
    )
    assert decided.status == TicketStatus.APPROVED.value
    assert decided.decided_by == settings.owner_id


def test_webhook_token_is_sanitized(settings: Settings) -> None:
    settings.webhook_secret = SecretStr("abc+def/ghi==_jkl-")
    assert settings.webhook_token == "abcdefghi_jkl-"
