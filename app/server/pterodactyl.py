"""Read-only-by-default Pterodactyl client API wrapper."""

from __future__ import annotations

import time
from pathlib import Path, PurePosixPath
from typing import Any, cast

import httpx

from app.config import Settings


class PanelError(RuntimeError):
    pass


class PanelUnavailable(PanelError):
    pass


class PanelForbidden(PanelError):
    pass


class TTLCache:
    def __init__(self) -> None:
        self._values: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, ttl: float) -> Any | None:
        item = self._values.get(key)
        if item is None:
            return None
        stored_at, value = item
        if time.monotonic() - stored_at > ttl:
            self._values.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._values[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._values.clear()


READABLE_FILES = {"latest.log", "server.properties"}
READABLE_SUFFIXES = {".yml", ".yaml", ".json", ".txt", ".log", ".properties", ".conf"}


def validate_read_path(path: str) -> str:
    raw = (path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or "\x00" in raw:
        raise PanelForbidden("Invalid file path")
    parts = PurePosixPath(raw).parts
    if any(part in {"..", "."} for part in parts):
        raise PanelForbidden("Path traversal is not allowed")
    normalized = str(PurePosixPath(*parts))
    if normalized in READABLE_FILES:
        return normalized
    if not normalized.startswith("plugins/"):
        raise PanelForbidden("This file is not in the readable allowlist")
    if PurePosixPath(normalized).suffix.lower() not in READABLE_SUFFIXES:
        raise PanelForbidden("This file type is not readable")
    return normalized


class PterodactylClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache = TTLCache()
        base = settings.panel_base.rstrip("/")
        # Both common panel layouts are accepted: /api/client and a server-scoped base.
        self._servers_base = base if base.endswith("/servers") else f"{base}/servers"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=5.0),
            headers={
                "Authorization": f"Bearer {settings.panel_key.get_secret_value()}",
                "Accept": "application/json",
                "User-Agent": "eva-bot/1.0",
            },
        )

    @property
    def ready(self) -> bool:
        return self.settings.panel_ready and bool(self.settings.server_id)

    def _server_url(self, suffix: str = "") -> str:
        sid = self.settings.server_id.strip()
        return f"{self._servers_base}/{sid}{suffix}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.ready:
            raise PanelUnavailable("Panel credentials are not configured")
        try:
            response = await self._client.request(
                method,
                self._server_url(path),
                params=params,
            )
        except httpx.HTTPError as exc:
            raise PanelUnavailable("Panel is unreachable") from exc
        if response.status_code == 401:
            raise PanelUnavailable("Panel rejected the API key")
        if response.status_code == 403:
            raise PanelForbidden("Panel denied the request")
        if response.status_code >= 500:
            raise PanelUnavailable(f"Panel returned {response.status_code}")
        if response.status_code >= 400:
            raise PanelForbidden(f"Panel returned {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise PanelError("Panel returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise PanelError("Panel returned an unexpected payload")
        return payload

    async def resources(self) -> dict[str, Any]:
        cached = self._cache.get("resources", 30)
        if cached is not None:
            return cast(dict[str, Any], cached)
        payload = await self._request("GET", "/resources")
        data = payload.get("attributes") or payload
        if not isinstance(data, dict):
            raise PanelError("Panel resources payload is malformed")
        self._cache.set("resources", data)
        return data

    async def details(self) -> dict[str, Any]:
        cached = self._cache.get("details", 60)
        if cached is not None:
            return cast(dict[str, Any], cached)
        payload = await self._request("GET", "")
        data = payload.get("attributes") or payload
        if not isinstance(data, dict):
            raise PanelError("Panel details payload is malformed")
        self._cache.set("details", data)
        return data

    async def list_backups(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/backups")
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        return [item.get("attributes", {}) for item in data if isinstance(item, dict)]

    async def list_files(self, directory: str = "/") -> list[dict[str, Any]]:
        directory = directory or "/"
        if ".." in directory or "\x00" in directory:
            raise PanelForbidden("Invalid directory")
        payload = await self._request("GET", "/files/list", params={"directory": directory})
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        result: list[dict[str, Any]] = []
        for item in data:
            attributes = item.get("attributes", {}) if isinstance(item, dict) else {}
            if isinstance(attributes, dict):
                result.append(attributes)
        return result

    async def file_contents(self, path: str, *, max_bytes: int = 1_000_000) -> str:
        safe_path = validate_read_path(path)
        payload = await self._request("GET", "/files/contents", params={"file": safe_path})
        attributes = payload.get("attributes") or {}
        content = attributes.get("content") if isinstance(attributes, dict) else None
        if content is None:
            raise PanelError("Panel returned no file content")
        if not isinstance(content, str):
            content = str(content)
        if len(content.encode("utf-8")) > max_bytes:
            raise PanelFileTooLarge("File is too large")
        return content

    async def latest_log(self, lines: int = 200) -> str:
        content = await self.file_contents("latest.log")
        tail = content.splitlines()[-max(1, lines) :]
        return "\n".join(tail)

    async def send_command(self, command: str) -> None:
        """Send an allowlisted command through the panel websocket API.

        Pterodactyl does not return command output; callers must read the
        console/log tail separately. This is used only as an owner-authorized
        fallback when the RCON TCP port is unavailable.
        """
        if not self.ready:
            raise PanelUnavailable("Panel credentials are not configured")
        if not command.strip() or len(command) > 300:
            raise PanelForbidden("Invalid command")
        try:
            response = await self._client.post(
                self._server_url("/command"),
                json={"command": command},
            )
        except httpx.HTTPError as exc:
            raise PanelUnavailable("Panel is unreachable") from exc
        if response.status_code == 401:
            raise PanelUnavailable("Panel rejected the API key")
        if response.status_code == 403:
            raise PanelForbidden("Panel denied the command")
        if response.status_code == 409:
            raise PanelUnavailable("Server is not running")
        if response.status_code >= 400:
            raise PanelForbidden(f"Panel returned {response.status_code}")
        self._cache.clear()

    async def close(self) -> None:
        await self._client.aclose()
        self._cache.clear()

    @staticmethod
    def backup_name(attributes: dict[str, Any]) -> str:
        name = attributes.get("name") or attributes.get("uuid") or "backup"
        return Path(str(name)).name


class PanelFileTooLarge(PanelError):
    pass
