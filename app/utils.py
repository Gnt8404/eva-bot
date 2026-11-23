"""Small shared formatting helpers."""

from __future__ import annotations

import html
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.config import PROJECT_ROOT


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def shorten(value: str | None, limit: int = 500) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def now_utc() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def fmt_dt(value: datetime | None, *, tz: str = "Europe/Moscow") -> str:
    moment = as_utc(value)
    if moment is None:
        return "—"
    local = moment.astimezone(ZoneInfo(tz))
    return local.strftime("%d.%m.%Y %H:%M") + " МСК"


def display_user(username: str | None, user_id: int, first_name: str | None = None) -> str:
    if username:
        return f"@{username} (id {user_id})"
    if first_name:
        return f"{first_name} (id {user_id})"
    return f"id {user_id}"


def project_path(*parts: str) -> str:
    return str(PROJECT_ROOT.joinpath(*parts))
