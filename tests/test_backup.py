"""Remote SQLite snapshot/restore tests."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from app.config import Settings
from app.db.remote_backup import BACKUP_FILE, RemoteBackup


class FakePanel:
    ready = True

    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    async def file_contents(self, path: str) -> str:
        return self.files[path]

    async def write_text_file(self, path: str, content: str) -> None:
        self.files[path] = content


def test_snapshot_roundtrip(tmp_path: Path, settings: Settings) -> None:
    source = tmp_path / "eva.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("create table sample (value text)")
        connection.execute("insert into sample values ('saved')")
        connection.commit()
    connection.close()

    panel = FakePanel()
    backup = RemoteBackup(settings, panel)  # type: ignore[arg-type]
    assert asyncio.run(backup.backup(str(source))) is True
    assert BACKUP_FILE in panel.files

    source.unlink()
    assert asyncio.run(backup.restore_if_missing(str(source))) is True
    with sqlite3.connect(source) as connection:
        row = connection.execute("select value from sample").fetchone()
    assert row == ("saved",)
