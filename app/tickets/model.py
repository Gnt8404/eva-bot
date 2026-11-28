"""Typed domain values shared by ticket handlers and services."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TicketKind(StrEnum):
    REPORT = "report"
    APPLICATION = "application"
    WHITELIST = "whitelist"
    DONATION = "donation"
    QUESTION = "question"
    SUPPORT = "support"


class TicketStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_INFO = "needs_info"
    PROBATION = "probation"
    RESERVE = "reserve"
    ANSWERED = "answered"


class DonationStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    ISSUE_FAILED = "issue_failed"


class Currency(StrEnum):
    RUB = "RUB"
    USD = "USD"
    UZS = "UZS"


@dataclass(frozen=True, slots=True)
class Package:
    id: str
    label: str
    seals: int
    rub: int
    stars: int
    usd: str
    uzs: int

    @property
    def is_game_pass(self) -> bool:
        return self.id == "game-pass"

    @property
    def amount_line(self) -> str:
        return f"{self.rub} ₽ / {self.usd} $ / {self.uzs:,} сум".replace(",", " ")


@dataclass(frozen=True, slots=True)
class Requisites:
    currency: Currency
    full_card: str
    last4: str

    @property
    def display(self) -> str:
        return f"{self.currency.value}: {self.full_card}"


@dataclass(frozen=True, slots=True)
class TicketDecision:
    accept: str
    reject: str
    extra: str | None = None
    extra_status: str | None = None


@dataclass(frozen=True, slots=True)
class FormField:
    key: str
    prompt: str
    max_length: int = 500
    optional: bool = False
    hint: str = ""


@dataclass(slots=True)
class ReportForm:
    values: dict[str, str] = field(default_factory=dict)
    screenshot_file_id: str | None = None
    screenshot_kind: str | None = None

    def as_payload(self) -> dict[str, str | None]:
        return {
            **self.values,
            "screenshot_file_id": self.screenshot_file_id,
            "screenshot_kind": self.screenshot_kind,
        }


@dataclass(slots=True)
class ApplicationForm:
    values: dict[str, str] = field(default_factory=dict)

    def as_payload(self) -> dict[str, str]:
        return dict(self.values)
