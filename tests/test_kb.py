"""Hybrid knowledge-base search, owner overrides and channel-post ingestion."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.kb.store import KnowledgeBase


async def test_static_faq_has_source_and_no_cards(settings: Settings, kb: KnowledgeBase) -> None:
    assert await kb.chunk_count() >= 70
    response = await kb.search("Какие правила сервера?")
    assert response.results
    first = response.results[0]
    assert first.source_label
    assert first.answer or first.content
    assert "2202 2083 6871 6909" not in first.content


async def test_unknown_fact_has_low_confidence(kb: KnowledgeBase) -> None:
    response = await kb.search("Сколько стоит алмаз в магазине?")
    assert response.confidence < 0.55 or not response.results


@pytest.mark.parametrize(
    ("question", "doc_id"),
    [
        ("Как работает Sorrow?", "faq:sorrow"),
        ("Как получить промокод?", "faq:promo"),
        ("Можно ли жениться?", "faq:wedding"),
    ],
)
async def test_faq_ranking_targets(kb: KnowledgeBase, question: str, doc_id: str) -> None:
    response = await kb.search(question)
    assert response.results
    assert response.results[0].doc_id == doc_id


async def test_owner_override_wins_immediately(kb: KnowledgeBase) -> None:
    question = "Где находится секретный портал?"
    override = await kb.add_override(
        question=question,
        answer="Секретный портал находится за спавном.",
        created_by=6343943842,
        keywords="секретный портал",
    )
    response = await kb.search(question)
    assert response.results
    top = response.results[0]
    assert top.from_override
    assert top.answer == "Секретный портал находится за спавном."
    assert override.id is not None

    await kb.deactivate_override(override.id)
    response_after = await kb.search(question)
    assert not any(item.from_override for item in response_after.results)


async def test_new_channel_post_becomes_searchable(kb: KnowledgeBase) -> None:
    text = (
        "Кристалл эха можно обменять у смотрителя катакомб. "
        "Кристалл эха выдаётся за прохождение третьего уровня. #Aetherlyn #события"
    )
    created = await kb.ingest_post(
        channel="test",
        channel_label="Тестовый канал",
        message_id=501,
        text=text,
        posted_at=datetime.now(UTC),
        author="tester",
        link="https://t.me/c/test/501",
        tags=["#Aetherlyn", "#события"],
    )
    assert created
    chunks_after_first = await kb.chunk_count()
    await kb.ingest_post(
        channel="test",
        channel_label="Тестовый канал",
        message_id=501,
        text=text,
    )
    assert await kb.chunk_count() == chunks_after_first
    await kb.ingest_post(
        channel="test",
        channel_label="Тестовый канал",
        message_id=502,
        text="Второй тестовый пост про кристалл эха. #Aetherlyn",
    )
    response = await kb.search("Кристалл эха за прохождение")
    assert response.results
    assert any("Кристалл эха" in item.content for item in response.results)
    assert await kb.post_count() >= 2


async def test_reindex_preserves_static_and_posts(kb: KnowledgeBase) -> None:
    before = await kb.chunk_count()
    stats = await kb.reindex()
    assert stats.chunks == before
    response = await kb.search("Paper 1.21.11")
    assert response.results


def test_admin_plugins_are_hidden_from_players(kb: KnowledgeBase) -> None:
    visible = {item["name"] for item in kb.plugins()}
    full = {item["name"] for item in kb.plugins(include_admin=True)}
    assert "LuckPerms" not in visible
    assert "WorldEdit" not in visible
    assert "LuckPerms" in full
    assert "WorldEdit" in full
    assert "Geyser" in visible
    assert "Floodgate" in visible
