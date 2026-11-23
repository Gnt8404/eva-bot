"""Shared fixtures for the Eva test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.db.session import Database
from app.kb.store import KnowledgeBase
from app.llm.service import AssistantService
from app.tickets.service import DonationService, TicketService


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh",
        data_dir=tmp_path,
        database_path=tmp_path / "eva.sqlite3",
        min_search_confidence=0.55,
        webhook_secret="test-webhook-secret",
        panel_key="",
        llm_api_key="",
    )


@pytest.fixture
async def database(settings: Settings):
    database = Database.from_settings(settings)
    await database.init_models()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def kb(database: Database, settings: Settings) -> KnowledgeBase:
    knowledge = KnowledgeBase(database, settings)
    await knowledge.ingest_static(force=True)
    return knowledge


@pytest.fixture
async def tickets(database: Database, settings: Settings) -> TicketService:
    return TicketService(database, settings)


@pytest.fixture
async def donations(
    database: Database, settings: Settings, tickets: TicketService
) -> DonationService:
    return DonationService(database, settings, tickets)


@pytest.fixture
async def assistant(
    database: Database,
    settings: Settings,
    kb: KnowledgeBase,
    tickets: TicketService,
) -> AssistantService:
    return AssistantService(
        database=database,
        settings=settings,
        knowledge=kb,
        tickets=tickets,
    )
