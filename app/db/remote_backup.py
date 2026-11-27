"""Persist the SQLite database on the Minecraft server's own disk.

Render's free filesystem is ephemeral. The game server already has durable
storage, and the Pterodactyl API can read/write one allowlisted backup file.
The bot restores on a fresh instance and uploads a compressed snapshot while
running. No game data or credentials are exposed by this module.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import logging
import os
import sqlite3
from pathlib import Path

from app.config import Settings
from app.server.pterodactyl import PanelError, PterodactylClient

logger = logging.getLogger(__name__)
BACKUP_FILE = "eva-backup.b64"
MAX_BACKUP_BYTES = 1_400_000


class RemoteBackup:
    def __init__(self, settings: Settings, panel: PterodactylClient | None) -> None:
        self.settings = settings
        self.panel = panel

    @property
    def enabled(self) -> bool:
        return bool(self.settings.remote_backup_enabled and self.panel and self.panel.ready)

    async def restore_if_missing(self, database_path: str | None) -> bool:
        if not self.enabled or not database_path:
            return False
        target = Path(database_path)
        if await asyncio.to_thread(self._nonempty, target):
            return False
        try:
            content = await self.panel.file_contents(BACKUP_FILE)  # type: ignore[union-attr]
            raw = gzip.decompress(base64.b64decode(content.encode("ascii"), validate=True))
            if len(raw) > MAX_BACKUP_BYTES:
                raise ValueError("backup is too large")
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".restore.tmp")
            tmp.write_bytes(raw)
            os.replace(tmp, target)
            logger.info("Remote database backup restored (%d bytes)", len(raw))
            return True
        except Exception as exc:
            logger.warning("Remote database restore skipped: %s", type(exc).__name__)
            return False

    async def backup(self, database_path: str | None) -> bool:
        if not self.enabled or not database_path:
            return False
        source = Path(database_path)
        if not await asyncio.to_thread(self._nonempty, source):
            return False
        try:
            payload = await asyncio.to_thread(self._snapshot, source)
            await self.panel.write_text_file(BACKUP_FILE, payload)  # type: ignore[union-attr]
            logger.info("Remote database backup uploaded")
            return True
        except (PanelError, OSError, sqlite3.Error, ValueError) as exc:
            logger.warning("Remote database backup failed: %s", type(exc).__name__)
            return False

    @staticmethod
    def _nonempty(path: Path) -> bool:
        return path.exists() and path.stat().st_size > 0

    @staticmethod
    def _snapshot(source: Path) -> str:
        target = source.with_suffix(".snapshot.sqlite3")
        src = sqlite3.connect(source)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            dst.commit()
            raw = target.read_bytes()
        finally:
            dst.close()
            src.close()
            target.unlink(missing_ok=True)
        if not raw or len(raw) > MAX_BACKUP_BYTES:
            raise ValueError("snapshot has an invalid size")
        return base64.b64encode(gzip.compress(raw, compresslevel=6)).decode("ascii")
