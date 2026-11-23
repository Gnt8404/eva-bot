"""Append-only audit trail."""

from __future__ import annotations

from app.db.models import Audit
from app.db.session import Database
from app.security.redaction import redact_text


class AuditService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def record(
        self,
        *,
        actor_id: int | None,
        action: str,
        target: str = "",
        result: str = "ok",
        details: str = "",
    ) -> None:
        safe_details = redact_text(details)[:4000]
        async with self.database.session() as session:
            session.add(
                Audit(
                    actor_id=actor_id,
                    action=action[:64],
                    target=target[:255],
                    result=result[:24],
                    details=safe_details,
                )
            )
