"""Background maintenance: ingest, status samples, digest, backups, health."""

from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import Settings
from app.db.models import StatusSample
from app.db.remote_backup import RemoteBackup
from app.db.session import Database
from app.kb.store import KnowledgeBase
from app.notify import Notifier
from app.server.assets import AssetService
from app.server.status import StatusService
from app.workers.digest import DigestService

logger = logging.getLogger(__name__)


class Workers:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        kb: KnowledgeBase,
        status: StatusService,
        notifier: Notifier,
        assets: AssetService,
        digest: DigestService,
        remote_backup: RemoteBackup,
    ) -> None:
        self.database = database
        self.settings = settings
        self.kb = kb
        self.status = status
        self.notifier = notifier
        self.assets = assets
        self.digest = digest
        self.remote_backup = remote_backup
        self.scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))
        self._alerted: dict[str, datetime] = {}

    def start(self) -> None:
        if self.scheduler.running:
            return
        self.scheduler.add_job(
            self._status_tick,
            "interval",
            minutes=self.settings.status_poll_minutes,
            id="status",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._incremental_ingest,
            "interval",
            minutes=self.settings.incremental_ingest_minutes,
            id="tg_ingest",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._full_reindex,
            "cron",
            hour=self.settings.full_reindex_hour_msk,
            minute=10,
            id="reindex",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._backup,
            "cron",
            hour=4,
            minute=20,
            id="backup",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._remote_backup,
            "interval",
            minutes=10,
            id="remote_backup",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self.digest.send_daily,
            "cron",
            hour=self.settings.digest_hour,
            minute=self.settings.digest_minute,
            id="digest_daily",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self.digest.send_weekly,
            "cron",
            day_of_week="mon",
            hour=self.settings.digest_hour,
            minute=min(59, self.settings.digest_minute + 5),
            id="digest_weekly",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._health_tick,
            "interval",
            minutes=self.settings.healthcheck_minutes,
            id="health",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()
        logger.info("Scheduler started with %d jobs", len(self.scheduler.get_jobs()))

    async def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        if self.remote_backup.enabled:
            await self.remote_backup.backup(self.database.sqlite_path)

    async def _remote_backup(self) -> None:
        try:
            await self.remote_backup.backup(self.database.sqlite_path)
        except Exception:
            logger.warning("Remote backup tick failed", exc_info=True)

    async def _alert(self, key: str, text: str) -> None:
        now = datetime.now(UTC)
        previous = self._alerted.get(key)
        if previous is not None and now - previous < timedelta(hours=1):
            return
        self._alerted[key] = now
        await self.notifier.owner(text)

    async def _status_tick(self) -> None:
        try:
            snapshot = await self.status.snapshot(force=True)
            async with self.database.session() as session:
                session.add(
                    StatusSample(
                        online=snapshot.online,
                        max_online=snapshot.max_online,
                        tps=snapshot.tps,
                        mspt=snapshot.mspt,
                        state="online" if snapshot.available else "unavailable",
                    )
                )
            try:
                await self.assets.sync()
            except Exception:
                logger.warning("Asset sync failed", exc_info=True)
        except Exception as exc:
            logger.exception("Status tick failed")
            await self._alert("status", f"Сбой обновления статуса: {type(exc).__name__}")

    async def _incremental_ingest(self) -> None:
        if not self.settings.telethon_ready:
            return
        try:
            from app.kb.ingest_tg import ingest_channels

            result = await ingest_channels(self.settings, self.database, full=False)
            if result["posts"]:
                logger.info("Incremental Telegram ingest: %s posts", result["posts"])
        except Exception as exc:
            logger.exception("Telegram ingestion failed")
            await self._alert("ingest", f"Сбой чтения каналов: {type(exc).__name__}")

    async def _full_reindex(self) -> None:
        try:
            stats = await self.kb.reindex()
            await self.notifier.owner(
                f"Полный реиндекс: страниц {stats.documents}, чанков {stats.chunks}."
            )
        except Exception as exc:
            logger.exception("Full reindex failed")
            await self._alert("reindex", f"Сбой реиндекса: {type(exc).__name__}")

    async def _backup(self) -> None:
        if not self.settings.is_sqlite:
            logger.info("Backup skipped: only the SQLite deployment is backed up by Eva")
            return
        source = self.settings.sqlite_path
        if not source.exists():
            await self._alert("backup", "Файл базы не найден, бэкап не создан.")
            return
        target_dir = self.settings.backup_path
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"eva-{datetime.now(UTC):%Y%m%d-%H%M%S}.sqlite3"
        try:
            await asyncio.to_thread(self._copy_sqlite, source, target)
            self._prune_backups(target_dir)
            logger.info("Database backup written: %s", target.name)
        except Exception as exc:
            logger.exception("Backup failed")
            await self._alert("backup", f"Сбой бэкапа: {type(exc).__name__}")

    @staticmethod
    def _copy_sqlite(source: Path, target: Path) -> None:
        with sqlite3.connect(source) as connection:
            connection.execute("PRAGMA wal_checkpoint(FULL)")
        shutil.copy2(source, target)

    def _prune_backups(self, directory: Path) -> None:
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.backup_retention_days)
        for item in directory.glob("eva-*.sqlite3"):
            try:
                modified = datetime.fromtimestamp(item.stat().st_mtime, tz=UTC)
            except OSError:
                continue
            if modified < cutoff:
                item.unlink(missing_ok=True)

    async def _health_tick(self) -> None:
        try:
            db_ok = await self.database.health()
            chunks = await self.kb.chunk_count()
            unanswered = await self.kb.unanswered_count()
            if not db_ok:
                await self._alert("health_db", "База данных недоступна.")
            if chunks == 0:
                await self._alert("health_kb", "База знаний пуста: нужен реиндекс.")
            if unanswered >= self.settings.unanswered_alert_threshold:
                await self._alert(
                    "health_questions",
                    f"Неотвеченных вопросов: {unanswered}. Нужен разбор.",
                )
        except Exception as exc:
            logger.exception("Health check failed")
            await self._alert("health", f"Сбой healthcheck: {type(exc).__name__}")
