"""Aggregated live server status with graceful degradation."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.server.mcrcon import RconClient, RconError
from app.server.pterodactyl import PanelError, PterodactylClient

_LIST_RE = re.compile(r"There are\s+(\d+)\s+of a max of\s+(\d+)\s+players online", re.IGNORECASE)
_TPS_RE = re.compile(r"TPS from last 1m, 5m, 15m:\s*([\d.]+)", re.IGNORECASE)
_MSPT_RE = re.compile(r"(?:Server tick times|MSPT).*?([\d.]+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ServerStatus:
    available: bool
    state: str = "unknown"
    online: int | None = None
    max_online: int | None = None
    tps: float | None = None
    mspt: float | None = None
    cpu: float | None = None
    memory_bytes: int | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source: str = ""
    error: str | None = None

    @property
    def checked_local(self) -> str:
        return self.checked_at.astimezone(ZoneInfo("Europe/Moscow")).strftime("%H:%M")

    def as_text(self, *, prefix: str = "Статус") -> str:
        if not self.available:
            return f"{prefix}: недоступен (панель/RCON не отвечают)."
        parts = [f"{prefix}: {self.state}"]
        if self.online is not None:
            suffix = f" из {self.max_online}" if self.max_online is not None else ""
            parts.append(f"онлайн {self.online}{suffix}")
        if self.tps is not None:
            parts.append(f"TPS {self.tps:.1f}")
        if self.mspt is not None:
            parts.append(f"MSPT {self.mspt:.1f}")
        if self.cpu is not None:
            parts.append(f"CPU {self.cpu:.1f}%")
        if self.memory_bytes is not None:
            parts.append(f"RAM {self.memory_bytes / (1024**3):.1f} ГБ")
        parts.append(f"на момент проверки {self.checked_local} МСК")
        return ", ".join(parts) + "."


class StatusService:
    def __init__(
        self,
        settings: Settings,
        panel: PterodactylClient | None = None,
        rcon: RconClient | None = None,
    ) -> None:
        self.settings = settings
        self.panel = panel
        self.rcon = rcon or RconClient(
            settings.rcon_host,
            settings.rcon_port,
            settings.rcon_password.get_secret_value(),
            timeout=3.0,
        )
        self._cached: ServerStatus | None = None
        self._cached_at = 0.0

    async def snapshot(self, *, force: bool = False) -> ServerStatus:
        ttl = max(5, self.settings.status_cache_seconds)
        if not force and self._cached is not None and time.monotonic() - self._cached_at < ttl:
            return self._cached
        status = await self._collect()
        self._cached = status
        self._cached_at = time.monotonic()
        return status

    async def _collect(self) -> ServerStatus:
        state = "unknown"
        cpu: float | None = None
        memory_bytes: int | None = None
        online: int | None = None
        max_online: int | None = None
        tps: float | None = None
        mspt: float | None = None
        errors: list[str] = []
        sources: list[str] = []
        if self.panel is not None and self.panel.ready:
            try:
                resources = await asyncio.wait_for(self.panel.resources(), timeout=4)
                state = str(resources.get("current_state") or resources.get("state") or "running")
                payload = resources.get("resources") or {}
                if isinstance(payload, dict):
                    cpu_value = payload.get("cpu_absolute")
                    cpu = float(cpu_value) if isinstance(cpu_value, (int, float)) else None
                    memory_value = payload.get("memory_bytes")
                    memory_bytes = (
                        int(memory_value) if isinstance(memory_value, (int, float)) else None
                    )
                sources.append("panel")
            except (PanelError, TimeoutError, OSError) as exc:
                errors.append(f"panel: {exc}")
        if self.settings.rcon_ready:
            list_output = await self._rcon("list")
            if list_output is not None:
                match = _LIST_RE.search(list_output)
                if match:
                    online, max_online = int(match.group(1)), int(match.group(2))
                    sources.append("rcon")
            tps_output = await self._rcon("tps")
            if tps_output:
                match = _TPS_RE.search(tps_output)
                if match:
                    tps = float(match.group(1))
            mspt_output = await self._rcon("mspt")
            if mspt_output:
                match = _MSPT_RE.search(mspt_output)
                if match:
                    mspt = float(match.group(1))
        if not sources:
            return ServerStatus(
                available=False,
                state="unknown",
                checked_at=datetime.now(UTC),
                error="; ".join(errors)[:300] or "интеграции не настроены",
            )
        return ServerStatus(
            available=True,
            state=state,
            online=online,
            max_online=max_online,
            tps=tps,
            mspt=mspt,
            cpu=cpu,
            memory_bytes=memory_bytes,
            checked_at=datetime.now(UTC),
            source="+".join(sources),
            error="; ".join(errors)[:300] or None,
        )

    async def _rcon(self, command: str) -> str | None:
        try:
            return await asyncio.wait_for(self.rcon.command(command), timeout=4)
        except (RconError, TimeoutError, OSError):
            return None

    async def latest_log(self, lines: int = 200) -> tuple[bool, str]:
        if self.panel is None or not self.panel.ready:
            return False, "Панель не настроена."
        try:
            text = await asyncio.wait_for(self.panel.latest_log(lines), timeout=8)
        except (PanelError, TimeoutError, OSError) as exc:
            return False, f"Лог недоступен: {exc}"
        return True, text[:6000]

    async def close(self) -> None:
        if self.panel is not None:
            await self.panel.close()
