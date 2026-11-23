"""Single source of truth for authorization decisions."""

from __future__ import annotations

from app.config import Settings
from app.db.models import Ticket


class PermissionDenied(PermissionError):
    """Raised when an actor attempts an action outside their role."""


def is_owner(settings: Settings, user_id: int) -> bool:
    return user_id == settings.owner_id


def is_readonly_admin(settings: Settings, user_id: int) -> bool:
    return user_id in settings.readonly_admin_ids and user_id != settings.owner_id


def is_admin(settings: Settings, user_id: int) -> bool:
    return is_owner(settings, user_id) or is_readonly_admin(settings, user_id)


def require_owner(settings: Settings, user_id: int, action: str = "action") -> None:
    if not is_owner(settings, user_id):
        raise PermissionDenied(f"Owner rights required for {action}")


def can_use_console(settings: Settings, user_id: int) -> bool:
    return is_owner(settings, user_id)


def can_decide_ticket(settings: Settings, user_id: int) -> bool:
    return is_owner(settings, user_id)


def can_view_ticket(settings: Settings, ticket: Ticket, user_id: int) -> bool:
    """Owner sees every ticket; a user sees only their own ticket."""

    if is_owner(settings, user_id):
        return True
    return ticket.user_id == user_id


def can_view_status(settings: Settings, user_id: int) -> bool:
    return is_admin(settings, user_id)


def can_view_latest_log(settings: Settings, user_id: int) -> bool:
    return is_admin(settings, user_id)


def can_broadcast(settings: Settings, user_id: int) -> bool:
    return is_owner(settings, user_id)
