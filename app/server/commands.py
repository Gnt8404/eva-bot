"""Allowlisted Minecraft console access for the owner only."""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass

from app.config import Settings
from app.security.access import PermissionDenied, require_owner
from app.security.audit import AuditService
from app.security.redaction import redact_text
from app.server.mcrcon import RconClient, RconError

MAX_COMMAND_LENGTH = 300
MAX_OUTPUT_LENGTH = 1500

_FORBIDDEN_RE = re.compile(r"[\x00-\x1f\x7f]")
_SIMPLE_COMMANDS = {
    "list",
    "tps",
    "mspt",
    "save-all",
    "restart",
    "stop",
    "backup",
    "plugins",
    "version",
}
_DESTRUCTIVE = {"restart", "stop", "backup"}
_SAY_RE = re.compile(r"^say\s+(\S.{0,199})$", re.IGNORECASE | re.DOTALL)
_PROMO_RE = re.compile(r"^promo\s+create\s+([A-Za-z0-9_-]{4,64})\s+(\d{1,6})$", re.IGNORECASE)
_LP_RE = re.compile(
    r"^lp\s+user\s+([A-Za-z0-9_]{3,16})\s+parent\s+set\s+([A-Za-z0-9_-]{1,32})$",
    re.IGNORECASE,
)


class ConsoleError(RuntimeError):
    pass


class ConsoleDenied(ConsoleError):
    pass


class ConfirmationExpired(ConsoleError):
    pass


@dataclass(frozen=True, slots=True)
class CommandSpec:
    command: str
    title: str
    destructive: bool = False
    needs_text: bool = False


@dataclass(slots=True)
class PendingCommand:
    token: str
    actor_id: int
    command: str
    created_at: float
    expires_at: float

    @property
    def destructive(self) -> bool:
        return is_destructive(self.command)


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("list", "Список игроков"),
    CommandSpec("tps", "TPS"),
    CommandSpec("mspt", "MSPT"),
    CommandSpec("whitelist list", "Whitelist"),
    CommandSpec("plugins", "Плагины"),
    CommandSpec("version", "Версия"),
    CommandSpec("save-all", "Сохранить мир"),
    CommandSpec("say <текст>", "Сообщение в чат", needs_text=True),
    CommandSpec("promo create <code> <seals>", "Создать промокод", needs_text=True),
    CommandSpec("lp user <ник> parent set <группа>", "Выдать группу", needs_text=True),
    CommandSpec("backup", "Бэкап", destructive=True),
    CommandSpec("restart", "Перезапуск", destructive=True),
    CommandSpec("stop", "Остановка", destructive=True),
)


def is_destructive(command: str) -> bool:
    first = command.strip().split(maxsplit=1)[0].lower()
    return first in _DESTRUCTIVE


def validate_command(raw: str) -> str:
    """Normalise a console command against the deterministic allowlist."""
    if not raw or not raw.strip():
        raise ConsoleDenied("Пустая команда")
    command = raw.strip()
    if len(command) > MAX_COMMAND_LENGTH:
        raise ConsoleDenied("Команда слишком длинная")
    if _FORBIDDEN_RE.search(command):
        raise ConsoleDenied("Управляющие символы запрещены")
    if ";" in command or "`" in command or "$(" in command:
        raise ConsoleDenied("Составные команды запрещены")
    lowered = command.lower()
    if lowered == "whitelist list":
        return "whitelist list"
    if lowered in _SIMPLE_COMMANDS:
        return lowered
    if _SAY_RE.match(command):
        text = _SAY_RE.match(command).group(1)  # type: ignore[union-attr]
        if text.startswith("/"):
            raise ConsoleDenied("Нельзя отправлять команды через say")
        return f"say {text}"
    promo = _PROMO_RE.match(command)
    if promo:
        code, seals_raw = promo.groups()
        seals = int(seals_raw)
        if seals < 0 or seals > 100_000:
            raise ConsoleDenied("Недопустимое количество печатей")
        return f"promo create {code} {seals}"
    lp = _LP_RE.match(command)
    if lp:
        nick, group = lp.groups()
        return f"lp user {nick} parent set {group}"
    raise ConsoleDenied("Команда отсутствует в allowlist")


def mask_output(output: str) -> str:
    clean = redact_text(output or "").strip()
    if not clean:
        return "(команда выполнена, ответ пустой)"
    if len(clean) > MAX_OUTPUT_LENGTH:
        clean = clean[: MAX_OUTPUT_LENGTH - 1] + "…"
    return clean


class ConsoleService:
    """Two-step owner console with a short-lived in-memory confirmation token."""

    def __init__(
        self,
        settings: Settings,
        audit: AuditService | None = None,
        *,
        rcon: RconClient | None = None,
    ) -> None:
        self.settings = settings
        self._audit = audit
        self.rcon = rcon or RconClient(
            settings.rcon_host,
            settings.rcon_port,
            settings.rcon_password.get_secret_value(),
        )
        self._pending: dict[str, PendingCommand] = {}

    async def _require_owner(self, actor_id: int, action: str, target: str) -> None:
        try:
            require_owner(self.settings, actor_id, action=action)
        except PermissionDenied:
            await self._record(actor_id, f"{action}_denied", target[:120], "denied")
            raise

    async def prepare(self, actor_id: int, raw: str) -> PendingCommand:
        await self._require_owner(actor_id, "console", raw)
        try:
            command = validate_command(raw)
        except ConsoleDenied:
            await self._record(actor_id, "console_denied", raw[:120], "error")
            raise
        now = time.monotonic()
        self._purge()
        token = secrets.token_urlsafe(9)
        pending = PendingCommand(
            token=token,
            actor_id=actor_id,
            command=command,
            created_at=now,
            expires_at=now + self.settings.confirmation_ttl_seconds,
        )
        self._pending[token] = pending
        await self._record(actor_id, "console_prepare", command[:120], "ok")
        return pending

    def get_pending(self, token: str) -> PendingCommand | None:
        self._purge()
        return self._pending.get(token)

    async def confirm(self, actor_id: int, token: str) -> str:
        await self._require_owner(actor_id, "console_confirm", token)
        self._purge()
        pending = self._pending.pop(token, None)
        if pending is None:
            await self._record(actor_id, "console_confirm", token[:32], "expired")
            raise ConfirmationExpired("Подтверждение истекло или не существует")
        if pending.actor_id != actor_id:
            await self._record(actor_id, "console_confirm_foreign", token[:32], "denied")
            raise ConsoleDenied("Это подтверждение создано другим пользователем")
        if time.monotonic() > pending.expires_at:
            raise ConfirmationExpired("Подтверждение истекло")
        output = await self._execute(actor_id, pending.command)
        return output

    async def cancel(self, actor_id: int, token: str) -> None:
        await self._require_owner(actor_id, "console_cancel", token)
        self._pending.pop(token, None)

    async def run_automated(self, actor_id: int, raw: str, *, purpose: str) -> str:
        """Execute an allowlisted command after an explicit owner confirmation event.

        Used for fulfilment of an already confirmed donation. The owner button press
        is the confirmation; there is no second interactive step.
        """
        await self._require_owner(actor_id, "console_automated", raw)
        command = validate_command(raw)
        await self._record(actor_id, f"console_automated:{purpose}", command[:120], "ok")
        return await self._execute(actor_id, command, audit=False)

    async def _execute(self, actor_id: int, command: str, *, audit: bool = True) -> str:
        if not self.settings.rcon_ready:
            await self._record(actor_id, "console_execute", command[:120], "unavailable")
            raise ConsoleError("RCON не настроен")
        try:
            raw_output = await self.rcon.command(command)
        except RconError as exc:
            await self._record(actor_id, "console_execute", command[:120], "error")
            raise ConsoleError(str(exc)) from exc
        output = mask_output(raw_output)
        if audit:
            await self._record(
                actor_id,
                "console_execute",
                command[:120],
                "ok",
                details=output[:500],
            )
        else:
            await self._record(actor_id, "console_executed", command[:120], "ok")
        return output

    def _purge(self) -> None:
        now = time.monotonic()
        for token, pending in list(self._pending.items()):
            if pending.expires_at < now:
                self._pending.pop(token, None)

    async def _record(
        self,
        actor_id: int,
        action: str,
        target: str,
        result: str,
        details: str = "",
    ) -> None:
        if self._audit is not None:
            await self._audit.record(
                actor_id=actor_id,
                action=action,
                target=target,
                result=result,
                details=details,
            )
