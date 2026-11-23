"""Application configuration loaded exclusively from environment variables."""

from __future__ import annotations

import hashlib
import hmac
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_path(value: Path) -> Path:
    return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()


class Settings(BaseSettings):
    """Runtime settings. No secret is ever embedded in log messages or API responses."""

    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # Telegram
    bot_token: SecretStr = SecretStr("")
    owner_id: int = 6343943842
    owner_username: str = "@belyak345"
    admin_ids: str = ""
    blacklist_ids: str = ""
    team_chat_id: int = 0
    bot_mode: Literal["webhook", "polling"] = "webhook"
    webhook_base_url: str = ""
    webhook_path: str = "/webhook"
    webhook_secret: SecretStr = SecretStr("")
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 8080
    # Render/Koyeb inject PORT and RENDER_EXTERNAL_URL automatically.
    port: int = 0
    render_external_url: str = ""
    required_channel_id: int = 0
    required_channel_url: str = ""
    drop_pending_updates: bool = False

    # LLM (OpenAI-compatible). Empty key means deterministic extractive mode.
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 30.0
    llm_max_tokens: int = 450
    # Free-form questions outside Aetherlyn are refused unless the owner opts in.
    # Keep the strict Aetherlyn-only mode as the safe default.
    allow_general_topics: bool = False

    # Storage and paths
    database_url: str = ""
    database_path: Path = Path("data/eva.sqlite3")
    data_dir: Path = Path("data")
    kb_dir: Path = Path("data/kb")
    backup_dir: Path = Path("backups")
    log_level: str = "INFO"
    log_json: bool = False
    auto_ingest_docs: bool = True

    # Anti-abuse
    throttle_per_minute: int = 5
    throttle_per_hour: int = 40
    throttle_mute_seconds: int = 600
    max_input_chars: int = 2000
    max_upload_bytes: int = 20 * 1024 * 1024
    max_answer_chars: int = 900

    # Knowledge base
    search_top_k: int = 6
    min_search_confidence: float = 0.55
    post_chunk_size: int = 700
    post_chunk_overlap: int = 80
    incremental_ingest_minutes: int = 10
    full_reindex_hour_msk: int = 4

    # Server integrations
    panel_base: str = "https://mgr.hosting-minecraft.pro/api/client"
    panel_key: SecretStr = SecretStr("")
    server_id: str = "acd21ae2-4cf0-49e2-89cc-d8f908f66505a"
    rcon_host: str = "127.0.0.1"
    rcon_port: int = 25575
    rcon_password: SecretStr = SecretStr("")
    server_host: str = ""
    server_port: int = 25646
    status_cache_seconds: int = 30
    status_poll_minutes: int = 15
    promo_command_template: str = "promo create {code} {seals}"
    console_output_limit: int = 1500
    confirmation_ttl_seconds: int = 30

    # Telethon reader (read-only)
    tg_api_id: int = 0
    tg_api_hash: SecretStr = SecretStr("")
    tg_session: SecretStr = SecretStr("")
    tg_channels_file: Path = Path("data/kb/channels.yaml")

    # Polls, backup, alerts
    timezone: str = "Europe/Moscow"
    digest_hour: int = 12
    digest_minute: int = 0
    backup_retention_days: int = 14
    unanswered_alert_threshold: int = 10
    healthcheck_minutes: int = 15

    # Output guards / links
    allowed_link_domains: str = "t.me,telegram.me,telegram.org"

    # Payment requisites. They live only in configuration, never in tickets or logs.
    payment_card_uzs: str = "5614 6818 1255 1210"
    payment_card_usd: str = "4231 2000 1844 1583"
    payment_card_rub: str = "2202 2083 6871 6909"

    @field_validator("webhook_path")
    @classmethod
    def _validate_webhook_path(cls, value: str) -> str:
        if not value.startswith("/"):
            return f"/{value}"
        return value

    @property
    def token(self) -> str:
        return self.bot_token.get_secret_value().strip()

    @property
    def readonly_admin_ids(self) -> set[int]:
        return _parse_ids(self.admin_ids)

    @property
    def blacklisted_ids(self) -> set[int]:
        return _parse_ids(self.blacklist_ids)

    @property
    def allowed_domains(self) -> tuple[str, ...]:
        values = {item.strip().lower() for item in self.allowed_link_domains.split(",")}
        return tuple(sorted(value for value in values if value))

    @property
    def data_path(self) -> Path:
        return _resolve_path(self.data_dir)

    @property
    def kb_path(self) -> Path:
        return _resolve_path(self.kb_dir)

    @property
    def backup_path(self) -> Path:
        return _resolve_path(self.backup_dir)

    @property
    def channels_path(self) -> Path:
        return _resolve_path(self.tg_channels_file)

    @property
    def sqlite_path(self) -> Path:
        return _resolve_path(self.database_path)

    @property
    def db_url(self) -> str:
        if self.database_url.strip():
            return self.database_url.strip()
        return f"sqlite+aiosqlite:///{self.sqlite_path.as_posix()}"

    @property
    def is_sqlite(self) -> bool:
        return self.db_url.startswith("sqlite")

    @property
    def llm_ready(self) -> bool:
        return bool(
            self.llm_base_url.strip()
            and self.llm_model.strip()
            and self.llm_api_key.get_secret_value().strip()
        )

    @property
    def panel_ready(self) -> bool:
        return bool(self.panel_base.strip() and self.panel_key.get_secret_value().strip())

    @property
    def rcon_ready(self) -> bool:
        return bool(self.rcon_host.strip() and self.rcon_password.get_secret_value().strip())

    @property
    def telethon_ready(self) -> bool:
        return bool(
            self.tg_api_id
            and self.tg_api_hash.get_secret_value().strip()
            and self.tg_session.get_secret_value().strip()
        )

    @property
    def public_address(self) -> str | None:
        host = self.server_host.strip()
        return host or None

    def is_owner(self, user_id: int) -> bool:
        return user_id == self.owner_id

    def is_readonly_admin(self, user_id: int) -> bool:
        return user_id in self.readonly_admin_ids and user_id != self.owner_id

    def payment_card(self, currency: str) -> str:
        cards = {
            "UZS": self.payment_card_uzs,
            "USD": self.payment_card_usd,
            "RUB": self.payment_card_rub,
        }
        try:
            return cards[currency.upper()].strip()
        except KeyError as exc:
            raise ValueError(f"Unknown currency: {currency}") from exc

    @property
    def webhook_base(self) -> str:
        return (self.webhook_base_url or self.render_external_url).strip().rstrip("/")

    @property
    def listen_port(self) -> int:
        return self.port or self.webhook_port

    @property
    def webhook_token(self) -> str:
        """Telegram allows only A-Z, a-z, 0-9, underscore and hyphen in secret_token.

        Render's generated ``WEBHOOK_SECRET`` can be unavailable/empty. In that case a
        stable token is derived from the bot token, which is itself secret, so webhook
        authentication stays mandatory instead of silently disabling it.
        """
        explicit = re.sub(r"[^A-Za-z0-9_-]", "", self.webhook_secret.get_secret_value())[:256]
        if explicit:
            return explicit
        if not self.token:
            return ""
        digest = hmac.new(
            self.token.encode("utf-8"), b"eva-webhook-secret-v1", hashlib.sha256
        ).hexdigest()
        return f"eva_{digest}"

    def webhook_url(self) -> str:
        return f"{self.webhook_base}{self.webhook_path}"


def _parse_ids(raw: str) -> set[int]:
    result: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        value = chunk.strip()
        if not value:
            continue
        try:
            result.add(int(value))
        except ValueError:
            continue
    return result


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
