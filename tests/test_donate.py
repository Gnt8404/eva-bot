"""Donation table, USD conversion, requisites and owner-only fulfilment."""

from __future__ import annotations

import pytest

from app.bot.media import image_magic
from app.config import Settings
from app.security.access import PermissionDenied
from app.tickets.model import Currency, DonationStatus
from app.tickets.service import DonationService


class FakeConsole:
    def __init__(self, output: str = "Promo created") -> None:
        self.output = output
        self.calls: list[str] = []

    async def run_automated(self, actor_id: int, raw: str, *, purpose: str) -> str:
        self.calls.append(raw)
        return self.output


def test_packages_and_fixed_usd_formula(donations: DonationService) -> None:
    packages = {package.id: package for package in donations.packages}
    assert len(packages) == 17
    assert packages["seals-5"].rub == 18
    assert donations.calculate_usd(18) == "0.20"
    assert packages["seals-100"].rub == 175
    assert donations.calculate_usd(175) == "1.95"
    assert packages["seals-1000"].rub == 1240
    assert donations.calculate_usd(1240) == "13.80"
    assert packages["game-pass"].rub == 200
    donations.validate_prices()


def test_amount_for_selected_currency(donations: DonationService) -> None:
    package = donations.package("seals-100")
    assert donations.amount_for(package, Currency.RUB) == "175 ₽"
    assert donations.amount_for(package, Currency.USD) == "1.95 $"
    assert donations.amount_for(package, Currency.UZS) == "49 000 сум"


def test_requisites_hide_full_card_from_public_fields(
    settings: Settings, donations: DonationService
) -> None:
    rub = donations.requisites(Currency.RUB)
    usd = donations.requisites(Currency.USD)
    uzs = donations.requisites(Currency.UZS)
    assert rub.full_card == "2202 2083 6871 6909"
    assert rub.last4 == "6909"
    assert usd.last4 == "1583"
    assert uzs.last4 == "1210"
    assert settings.is_owner(settings.owner_id)
    assert "2202" not in rub.currency.value


def test_image_magic_rejects_html_and_archives() -> None:
    assert image_magic(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert image_magic(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert image_magic(b"RIFF0000WEBPrest") == "image/webp"
    assert image_magic(b"<html><script>alert(1)</script>") is None
    assert image_magic(b"PK\x03\x04zip") is None


async def test_application_has_no_full_card_and_no_auto_promo(
    settings: Settings, donations: DonationService
) -> None:
    ticket, donation = await donations.create_application(
        user_id=777,
        username="buyer",
        nickname="Steve",
        package_id="seals-100",
        screenshot_file_id="screen-file",
        screenshot_kind="photo",
    )
    assert donation.status == DonationStatus.PENDING.value
    assert donation.amount_rub == 175
    assert donation.amount_usd == 1.95
    assert donation.promo_code is None
    assert ticket.kind == "donation"
    serialized = f"{ticket.subject} {ticket.payload}"
    for digits in ("2202 2083 6871 6909", "5614 6818 1255 1210", "4231 2000 1844 1583"):
        assert digits not in serialized


async def test_owner_confirm_generates_and_issues_promo(
    settings: Settings, donations: DonationService
) -> None:
    _, donation = await donations.create_application(
        user_id=778,
        username="buyer2",
        nickname="Alex",
        package_id="seals-100",
        screenshot_file_id="screen-file",
        screenshot_kind="photo",
    )
    console = FakeConsole("Success")
    result = await donations.confirm(
        donation.id,
        decided_by=settings.owner_id,
        console=console,  # type: ignore[arg-type]
    )
    assert result.ok
    assert result.promo is not None
    assert result.promo.startswith("AETHER-100-")
    assert len(result.promo.rsplit("-", 1)[1]) == 8
    assert console.calls == [f"promo create {result.promo} 100"]
    fresh = await donations.get(donation.id)
    assert fresh is not None
    assert fresh.status == DonationStatus.CONFIRMED.value
    assert fresh.promo_issued is True


async def test_non_owner_cannot_confirm_donation(
    settings: Settings, donations: DonationService
) -> None:
    _, donation = await donations.create_application(
        user_id=779,
        username="buyer3",
        nickname="Herobrine",
        package_id="game-pass",
        screenshot_file_id=None,
        screenshot_kind=None,
    )
    with pytest.raises(PermissionDenied):
        await donations.confirm(donation.id, decided_by=111111, console=FakeConsole())  # type: ignore[arg-type]
    fresh = await donations.get(donation.id)
    assert fresh is not None
    assert fresh.promo_code is None
    assert fresh.promo_issued is False


async def test_console_failure_marks_promo_not_issued(
    settings: Settings, donations: DonationService
) -> None:
    _, donation = await donations.create_application(
        user_id=780,
        username="buyer4",
        nickname="Notch",
        package_id="seals-5",
        screenshot_file_id="screen",
        screenshot_kind="photo",
    )
    result = await donations.confirm(
        donation.id,
        decided_by=settings.owner_id,
        console=FakeConsole(""),  # type: ignore[arg-type]
    )
    assert not result.ok
    fresh = await donations.get(donation.id)
    assert fresh is not None
    assert fresh.status == DonationStatus.ISSUE_FAILED.value
    assert fresh.promo_issued is False
    assert fresh.error
