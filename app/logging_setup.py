"""Logging with mandatory secret and card redaction."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import PROJECT_ROOT, Settings
from app.security.redaction import redact_text, register_secret


class RedactingFormatter(logging.Formatter):
    """Format a record and redact the complete result, including tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def _register_settings_secrets(settings: Settings) -> None:
    for secret in (
        settings.token,
        settings.llm_api_key.get_secret_value(),
        settings.panel_key.get_secret_value(),
        settings.rcon_password.get_secret_value(),
        settings.webhook_secret.get_secret_value(),
        settings.tg_api_hash.get_secret_value(),
        settings.tg_session.get_secret_value(),
        settings.payment_card_rub,
        settings.payment_card_usd,
        settings.payment_card_uzs,
    ):
        register_secret(secret)


def configure_logging(settings: Settings) -> None:
    _register_settings_secrets(settings)
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = RedactingFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    handlers: list[logging.Handler] = [stream]

    logs_dir = PROJECT_ROOT / "logs"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            logs_dir / "eva.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except OSError:
        # A read-only container may not allow a log file; stdout remains.
        pass

    logging.basicConfig(level=level, handlers=handlers, force=True)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def safe_settings_summary(settings: Settings) -> dict[str, str | int | bool]:
    """Non-secret facts useful in a startup log."""

    return {
        "mode": settings.bot_mode,
        "owner_id": settings.owner_id,
        "database": "sqlite" if settings.is_sqlite else "external",
        "llm_ready": settings.llm_ready,
        "panel_ready": settings.panel_ready,
        "rcon_ready": settings.rcon_ready,
        "telethon_ready": settings.telethon_ready,
        "server_host_configured": bool(settings.server_host),
        "data_dir": str(Path(settings.data_path)),
    }
