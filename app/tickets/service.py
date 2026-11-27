"""Ticket and donation application services."""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.config import Settings
from app.db.models import Donation, Ticket
from app.db.session import Database
from app.kb.ingest_docs import load_donation
from app.security.access import PermissionDenied, require_owner
from app.security.audit import AuditService
from app.tickets.model import (
    Currency,
    DonationStatus,
    Package,
    Requisites,
    TicketKind,
    TicketStatus,
)

if TYPE_CHECKING:
    from app.server.commands import ConsoleService

PROMO_ALPHABET = string.ascii_uppercase + string.digits
PROMO_LENGTH = 8


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    ok: bool
    donation_id: int
    promo: str | None
    error: str | None = None


class TicketService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        audit: AuditService | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.audit = audit or AuditService(database)

    async def create(
        self,
        *,
        kind: TicketKind,
        user_id: int,
        subject: str = "",
        payload: dict[str, object] | None = None,
    ) -> Ticket:
        ticket = Ticket(
            kind=kind.value,
            user_id=user_id,
            subject=subject[:255],
            payload=payload or {},
            status=TicketStatus.PENDING.value,
        )
        async with self.database.session() as session:
            session.add(ticket)
            await session.flush()
            await session.refresh(ticket)
        await self.audit.record(
            actor_id=user_id,
            action=f"ticket_create:{kind.value}",
            target=str(ticket.id),
            result="ok",
            details=subject[:200],
        )
        return ticket

    async def get(self, ticket_id: int) -> Ticket | None:
        async with self.database.session() as session:
            return await session.get(Ticket, ticket_id)

    async def list_pending(self, limit: int = 10) -> list[Ticket]:
        async with self.database.session() as session:
            result = await session.execute(
                select(Ticket)
                .where(Ticket.status == TicketStatus.PENDING.value)
                .order_by(Ticket.id.desc())
                .limit(limit)
            )
            return list(result.scalars())

    async def count_open(self) -> int:
        async with self.database.session() as session:
            result = await session.execute(
                select(func.count(Ticket.id)).where(
                    Ticket.status.in_(
                        (
                            TicketStatus.PENDING.value,
                            TicketStatus.NEEDS_INFO.value,
                            TicketStatus.PROBATION.value,
                        )
                    )
                )
            )
            return int(result.scalar_one() or 0)

    async def decide(
        self,
        ticket_id: int,
        *,
        status: TicketStatus,
        decided_by: int,
        note: str | None = None,
    ) -> Ticket:
        require_owner(self.settings, decided_by, action="ticket_decision")
        if status not in {
            TicketStatus.APPROVED,
            TicketStatus.REJECTED,
            TicketStatus.NEEDS_INFO,
            TicketStatus.PROBATION,
            TicketStatus.RESERVE,
            TicketStatus.ANSWERED,
        }:
            raise ValueError(f"Unsupported ticket status: {status}")
        async with self.database.session() as session:
            ticket = await session.get(Ticket, ticket_id)
            if ticket is None:
                raise LookupError("Ticket not found")
            ticket.status = status.value
            ticket.decided_by = decided_by
            ticket.decision = status.value
            ticket.decision_note = note
        await self.audit.record(
            actor_id=decided_by,
            action=f"ticket_decision:{status.value}",
            target=str(ticket_id),
            result="ok",
            details=note or "",
        )
        return ticket


class DonationService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        tickets: TicketService,
        audit: AuditService | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.tickets = tickets
        self.audit = audit or AuditService(database)
        raw = load_donation(settings)
        self.usd_rate = int(raw.get("usd_rate") or 90)
        self.source_label = str(raw.get("source_label") or "#Aetherlyn #donate")
        self.message = str(raw.get("message") or "").strip()
        self._packages: dict[str, Package] = {}
        for item in raw.get("packages") or []:
            package = Package(
                id=str(item["id"]),
                label=str(item["label"]),
                seals=int(item["seals"]),
                rub=int(item["rub"]),
                stars=int(item["stars"]),
                usd=f"{float(item['usd']):.2f}",
                uzs=int(item["uzs"]),
            )
            self._packages[package.id] = package
        if not self._packages:
            raise RuntimeError("No donation packages configured")

    @property
    def packages(self) -> list[Package]:
        return list(self._packages.values())

    def package(self, package_id: str) -> Package:
        try:
            return self._packages[package_id]
        except KeyError as exc:
            raise ValueError("Unknown donation package") from exc

    def calculate_usd(self, rub: int) -> str:
        if rub <= 0:
            raise ValueError("Amount must be positive")
        raw = Decimal(rub) / Decimal(self.usd_rate)
        # Round to the nearest 0.05 dollar, not merely to two decimal places.
        steps = (raw * 20).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        amount = steps / 20
        return f"{amount:.2f}"

    def validate_prices(self) -> None:
        """Ensure every YAML package matches the fixed RUB/USD formula."""
        errors: list[str] = []
        for package in self._packages.values():
            try:
                expected = Decimal(self.calculate_usd(package.rub))
            except (InvalidOperation, ValueError):
                errors.append(f"{package.id}: invalid RUB amount")
                continue
            if Decimal(package.usd) != expected:
                errors.append(f"{package.id}: expected {expected}, got {package.usd}")
        if errors:
            raise ValueError("Donation price table is inconsistent: " + "; ".join(errors))

    def requisites(self, currency: Currency | str) -> Requisites:
        normalized = Currency(str(currency).upper())
        card = self.settings.payment_card(normalized.value)
        digits = "".join(ch for ch in card if ch.isdigit())
        if len(digits) < 4:
            raise RuntimeError("Payment card is not configured")
        return Requisites(currency=normalized, full_card=card, last4=digits[-4:])

    def line_for_package(self, package: Package) -> str:
        return f"{package.label} - {package.rub} ₽\\{package.stars} ⭐️\\{package.usd} $\\{package.uzs:,} сум".replace(
            ",", " "
        )

    def amount_for(self, package: Package, currency: Currency | str) -> str:
        """Human-readable package amount in the currency the player selected."""
        normalized = Currency(str(currency).upper())
        if normalized is Currency.RUB:
            return f"{package.rub} ₽"
        if normalized is Currency.USD:
            return f"{package.usd} $"
        return f"{package.uzs:,} сум".replace(",", " ")

    async def create_application(
        self,
        *,
        user_id: int,
        username: str | None,
        nickname: str,
        package_id: str,
        screenshot_file_id: str | None,
        screenshot_kind: str | None,
    ) -> tuple[Ticket, Donation]:
        package = self.package(package_id)
        ticket = await self.tickets.create(
            kind=TicketKind.DONATION,
            user_id=user_id,
            subject=f"{package.label} — {package.rub} ₽",
            payload={
                "nickname": nickname[:32],
                "tg_username": (username or "")[:64],
                "package_id": package.id,
                "package_label": package.label,
                "has_screenshot": bool(screenshot_file_id),
            },
        )
        donation = Donation(
            ticket_id=ticket.id,
            user_id=user_id,
            nickname=nickname[:32],
            tg_username=(username or "")[:64],
            package_id=package.id,
            package_label=package.label,
            seals=package.seals,
            amount_rub=package.rub,
            amount_usd=float(package.usd),
            amount_uzs=package.uzs,
            stars=package.stars,
            screenshot_file_id=screenshot_file_id,
            screenshot_kind=screenshot_kind,
            status=DonationStatus.PENDING.value,
        )
        async with self.database.session() as session:
            session.add(donation)
            await session.flush()
            await session.refresh(donation)
        await self.audit.record(
            actor_id=user_id,
            action="donation_application",
            target=str(donation.id),
            result="ok",
            details=f"{package.id}; ticket={ticket.id}",
        )
        return ticket, donation

    async def get(self, donation_id: int) -> Donation | None:
        async with self.database.session() as session:
            return await session.get(Donation, donation_id)

    async def reject(self, donation_id: int, *, decided_by: int, note: str = "") -> Donation:
        require_owner(self.settings, decided_by, action="donation_reject")
        async with self.database.session() as session:
            donation = await session.get(Donation, donation_id)
            if donation is None:
                raise LookupError("Donation not found")
            donation.status = DonationStatus.REJECTED.value
            donation.error = note or "rejected by owner"
            if donation.ticket_id:
                ticket = await session.get(Ticket, donation.ticket_id)
                if ticket is not None:
                    ticket.status = TicketStatus.REJECTED.value
                    ticket.decision = "donation_rejected"
                    ticket.decided_by = decided_by
        await self.audit.record(
            actor_id=decided_by,
            action="donation_reject",
            target=str(donation_id),
            result="ok",
            details=note,
        )
        return donation

    async def ask_for_receipt(self, donation_id: int, *, decided_by: int) -> Donation:
        require_owner(self.settings, decided_by, action="donation_recheck")
        async with self.database.session() as session:
            donation = await session.get(Donation, donation_id)
            if donation is None:
                raise LookupError("Donation not found")
            if donation.ticket_id:
                ticket = await session.get(Ticket, donation.ticket_id)
                if ticket is not None:
                    ticket.status = TicketStatus.NEEDS_INFO.value
                    ticket.decision = "receipt_requested"
                    ticket.decided_by = decided_by
        await self.audit.record(
            actor_id=decided_by,
            action="donation_request_receipt",
            target=str(donation_id),
            result="ok",
            details="",
        )
        return donation

    async def confirm(
        self,
        donation_id: int,
        *,
        decided_by: int,
        console: ConsoleService | None = None,
    ) -> ConfirmationResult:
        require_owner(self.settings, decided_by, action="donation_confirm")
        donation = await self.get(donation_id)
        if donation is None:
            raise LookupError("Donation not found")
        if donation.status == DonationStatus.CONFIRMED.value and donation.promo_issued:
            return ConfirmationResult(True, donation.id, donation.promo_code, None)
        if donation.status == DonationStatus.REJECTED.value:
            raise ValueError("Donation is rejected")
        package = self.package(donation.package_id)
        promo = await self._new_promo(package)
        command = self.settings.promo_command_template.format(
            code=promo, seals=package.seals
        ).strip()
        error: str | None = None
        issued = False
        if console is None:
            error = "RCON/console is not configured"
        else:
            try:
                output = await console.run_automated(decided_by, command, purpose="donation_promo")
                issued = bool(output and output.strip())
                if not issued:
                    error = "console returned no response"
            except Exception as exc:
                error = str(exc)[:300]
        async with self.database.session() as session:
            row = await session.get(Donation, donation_id)
            if row is None:
                raise LookupError("Donation not found")
            row.promo_code = promo
            row.promo_issued = issued
            row.confirmed_by = decided_by
            row.confirmed_at = datetime.now(UTC)
            row.status = (
                DonationStatus.CONFIRMED.value if issued else DonationStatus.ISSUE_FAILED.value
            )
            row.error = error
            if row.ticket_id:
                ticket = await session.get(Ticket, row.ticket_id)
                if ticket is not None:
                    ticket.status = (
                        TicketStatus.APPROVED.value if issued else TicketStatus.PENDING.value
                    )
                    ticket.decision = "confirmed" if issued else "issue_failed"
                    ticket.decided_by = decided_by
        await self.audit.record(
            actor_id=decided_by,
            action="donation_confirm",
            target=str(donation_id),
            result="ok" if issued else "error",
            details=f"promo={promo}; issued={issued}; error={error or ''}",
        )
        return ConfirmationResult(issued, donation_id, promo, error)

    async def receive_new_receipt(
        self,
        donation_id: int,
        *,
        file_id: str,
        kind: str,
    ) -> Donation:
        async with self.database.session() as session:
            donation = await session.get(Donation, donation_id)
            if donation is None:
                raise LookupError("Donation not found")
            donation.screenshot_file_id = file_id
            donation.screenshot_kind = kind
            donation.status = DonationStatus.PENDING.value
            donation.error = None
            if donation.ticket_id:
                ticket = await session.get(Ticket, donation.ticket_id)
                if ticket is not None:
                    ticket.status = TicketStatus.PENDING.value
                    ticket.decision = "receipt_received"
        await self.audit.record(
            actor_id=None,
            action="donation_receipt_received",
            target=str(donation_id),
            result="ok",
            details=kind,
        )
        return donation

    async def _new_promo(self, package: Package) -> str:
        label = "PASS" if package.is_game_pass else str(package.seals)
        for _ in range(12):
            suffix = "".join(secrets.choice(PROMO_ALPHABET) for _ in range(PROMO_LENGTH))
            candidate = f"AETHER-{label}-{suffix}"
            async with self.database.session() as session:
                exists = await session.scalar(
                    select(Donation.id).where(Donation.promo_code == candidate)
                )
            if not exists:
                return candidate
        raise RuntimeError("Could not allocate a unique promo code")

    async def confirmed_totals(self, *, since: datetime | None = None) -> dict[str, float]:
        start = since or datetime.now(UTC) - timedelta(days=1)
        async with self.database.session() as session:
            result = await session.execute(
                select(
                    func.count(Donation.id),
                    func.coalesce(func.sum(Donation.amount_rub), 0),
                    func.coalesce(func.sum(Donation.amount_usd), 0.0),
                ).where(
                    Donation.status == DonationStatus.CONFIRMED.value,
                    Donation.confirmed_at.is_not(None),
                    Donation.confirmed_at >= start,
                )
            )
            count, rub, usd = result.one()
        return {"count": int(count or 0), "rub": float(rub or 0), "usd": float(usd or 0)}


__all__ = [
    "ConfirmationResult",
    "DonationService",
    "PermissionDenied",
    "TicketService",
]
