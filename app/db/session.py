"""Async SQLAlchemy engine and session helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.db.models import Base

FTS_TRIGGERS = (
    """
    CREATE TRIGGER IF NOT EXISTS kb_chunks_ai AFTER INSERT ON kb_chunks BEGIN
        INSERT INTO kb_chunks_fts(rowid, search_text) VALUES (new.id, new.search_text);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS kb_chunks_ad AFTER DELETE ON kb_chunks BEGIN
        DELETE FROM kb_chunks_fts WHERE rowid = old.id;
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS kb_chunks_au AFTER UPDATE OF search_text ON kb_chunks BEGIN
        DELETE FROM kb_chunks_fts WHERE rowid = old.id;
        INSERT INTO kb_chunks_fts(rowid, search_text) VALUES (new.id, new.search_text);
    END
    """,
)


class Database:
    """Owns the async engine, session factory and SQLite search index."""

    def __init__(self, url: str, *, sqlite_path: str | None = None) -> None:
        self.url = url
        connect_args: dict[str, Any] = {}
        if url.startswith("sqlite"):
            connect_args = {"timeout": 30}
        self.engine: AsyncEngine = create_async_engine(
            url,
            echo=False,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        self.sqlite_path = sqlite_path
        self._session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )
        if url.startswith("sqlite"):
            self._install_sqlite_pragmas()

    @classmethod
    def from_settings(cls, settings: Settings) -> Database:
        sqlite_path = str(settings.sqlite_path) if settings.is_sqlite else None
        return cls(settings.db_url, sqlite_path=sqlite_path)

    def _install_sqlite_pragmas(self) -> None:
        @event.listens_for(self.engine.sync_engine, "connect")
        def _set_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    async def init_models(self) -> None:
        if self.sqlite_path:
            from pathlib import Path

            Path(self.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            if self.url.startswith("sqlite"):
                await connection.exec_driver_sql(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks_fts "
                    "USING fts5(search_text, tokenize='unicode61 remove_diacritics 2')"
                )
                for statement in FTS_TRIGGERS:
                    await connection.exec_driver_sql(statement)
                await connection.exec_driver_sql(
                    "INSERT INTO kb_chunks_fts(rowid, search_text) "
                    "SELECT id, search_text FROM kb_chunks "
                    "WHERE id NOT IN (SELECT rowid FROM kb_chunks_fts)"
                )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        session = self._session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def health(self) -> bool:
        try:
            async with self.session() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self.engine.dispose()
