"""Deterministic guard → retrieval → LLM → output-validation pipeline."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.config import Settings
from app.db.models import User
from app.db.session import Database
from app.kb.learn import LearningService
from app.kb.search import SearchEngine, SearchResponse, SearchResult
from app.kb.store import KnowledgeBase
from app.llm.client import LLMClient, LLMError
from app.llm.guard import (
    FRAUD_ANSWER,
    GENERAL_ERROR_ANSWER,
    INJECTION_ANSWER,
    NO_FACT_ANSWER,
    OFF_TOPIC_ANSWER,
    PRIVATE_ANSWER,
    PRIVILEGE_ANSWER,
    TOO_LONG_ANSWER,
    assess_topic,
    detect_fraud,
    detect_identity_claim,
    detect_injection,
    detect_private_data_request,
    detect_privileged_request,
    looks_like_too_long,
    make_source_line,
    sanitize_input,
    validate_output,
    wrap_user_data,
)
from app.llm.prompts import (
    GENERAL_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_general_prompt,
    build_user_prompt,
)
from app.security.audit import AuditService
from app.tickets.model import TicketKind
from app.tickets.service import TicketService

logger = logging.getLogger(__name__)
_EMOJI_RE = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\u2b00-\u2bff\ufe0f\u200d\u20e3]")


class AnswerKind(StrEnum):
    FACT = "fact"
    GENERAL = "general"
    NO_FACT = "no_fact"
    REFUSAL = "refusal"
    INJECTION = "injection"
    TOO_LONG = "too_long"
    ERROR = "error"


@dataclass(slots=True)
class AnswerResult:
    text: str
    kind: AnswerKind
    confidence: float = 0.0
    ticket_id: int | None = None
    sources: list[str] = field(default_factory=list)
    blocked_reason: str | None = None


def _strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text).strip()


def _with_source(answer: str, result: SearchResult) -> str:
    clean = _strip_emoji(answer).strip()
    if re.search(r"(?:Источник\s*:|\B#Aetherlyn\b)", clean, re.IGNORECASE):
        return clean
    source = make_source_line(result.source_label, result.source_url)
    return f"{clean}\n{source}".strip()


class AssistantService:
    def __init__(
        self,
        *,
        database: Database,
        settings: Settings,
        knowledge: KnowledgeBase,
        llm: LLMClient | None = None,
        learning: LearningService | None = None,
        tickets: TicketService | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.knowledge = knowledge
        self.llm = llm
        self.learning = learning or LearningService(database, knowledge)
        self.tickets = tickets
        self.audit = AuditService(database)
        self.search = SearchEngine(database, settings)

    async def answer(
        self,
        user_id: int,
        question: str,
        *,
        live_status: str | None = None,
    ) -> AnswerResult:
        raw = question or ""
        if looks_like_too_long(raw) and len(raw) > self.settings.max_input_chars:
            return AnswerResult(TOO_LONG_ANSWER, AnswerKind.TOO_LONG)
        clean = sanitize_input(raw, self.settings.max_input_chars)
        if not clean:
            return AnswerResult(NO_FACT_ANSWER, AnswerKind.NO_FACT)
        injection = detect_injection(clean)
        if injection.triggered:
            await self._violation(user_id, "prompt_injection", ",".join(injection.reasons))
            return AnswerResult(
                INJECTION_ANSWER,
                AnswerKind.INJECTION,
                blocked_reason="injection",
            )
        if detect_identity_claim(clean):
            await self._violation(user_id, "identity_spoof", "identity_claim")
            return AnswerResult(
                PRIVILEGE_ANSWER,
                AnswerKind.REFUSAL,
                blocked_reason="identity_spoof",
            )
        if detect_private_data_request(clean):
            await self._violation(user_id, "private_data_request", "private")
            return AnswerResult(
                PRIVATE_ANSWER,
                AnswerKind.REFUSAL,
                blocked_reason="private_data",
            )
        if detect_fraud(clean):
            return AnswerResult(FRAUD_ANSWER, AnswerKind.REFUSAL, blocked_reason="fraud")
        if detect_privileged_request(clean):
            await self._violation(user_id, "privileged_request", "privileged")
            return AnswerResult(
                PRIVILEGE_ANSWER,
                AnswerKind.REFUSAL,
                blocked_reason="privileged",
            )
        topic = assess_topic(clean)
        if not topic.on_topic:
            if self.settings.allow_general_topics and self.llm is not None and self.llm.enabled:
                return await self._general_answer(user_id, clean)
            return AnswerResult(OFF_TOPIC_ANSWER, AnswerKind.REFUSAL, blocked_reason="off_topic")
        donation = self._donation_fact(clean)
        if donation is not None:
            return AnswerResult(
                donation,
                AnswerKind.FACT,
                confidence=1.0,
                sources=["#Aetherlyn #donate"],
            )
        response = await self.search.search(clean)
        if not response.results:
            return await self._no_fact(user_id, clean, response, live_status)
        best = response.results[0]
        if self.llm is not None and self.llm.enabled:
            answer = await self._llm_answer(user_id, clean, response, best, live_status)
            if answer is not None:
                return answer
        deterministic = self._deterministic(best)
        if deterministic is None:
            return await self._no_fact(user_id, clean, response, live_status)
        return AnswerResult(
            deterministic,
            AnswerKind.FACT,
            confidence=response.confidence,
            sources=[best.source_label or best.source_url],
        )

    async def _general_answer(self, user_id: int, question: str) -> AnswerResult:
        """Free-form answer for off-topic questions when the owner opted in."""
        if self.llm is None or not self.llm.enabled:
            return AnswerResult(OFF_TOPIC_ANSWER, AnswerKind.REFUSAL, blocked_reason="off_topic")
        try:
            raw = await self.llm.complete(
                system=GENERAL_SYSTEM_PROMPT,
                user=build_general_prompt(question),
            )
        except LLMError:
            logger.warning("General LLM answer failed")
            return AnswerResult(
                GENERAL_ERROR_ANSWER,
                AnswerKind.ERROR,
                blocked_reason="llm_error",
            )
        text = _strip_emoji(raw).strip()
        if not text:
            return AnswerResult(
                GENERAL_ERROR_ANSWER,
                AnswerKind.ERROR,
                blocked_reason="empty_llm_answer",
            )
        validation = validate_output(
            text,
            allowed_domains=self.settings.allowed_domains,
            max_chars=self.settings.max_answer_chars,
            require_source=False,
        )
        if not validation.ok and "too_many_lines" in validation.reasons:
            shortened = "\n".join(text.splitlines()[:6]).strip()
            validation = validate_output(
                shortened,
                allowed_domains=self.settings.allowed_domains,
                max_chars=self.settings.max_answer_chars,
                require_source=False,
            )
        if not validation.ok:
            if "secret_leak" in validation.reasons or "internal_prompt_leak" in validation.reasons:
                await self._violation(user_id, "output_leak", ",".join(validation.reasons))
                return AnswerResult(
                    PRIVATE_ANSWER,
                    AnswerKind.REFUSAL,
                    blocked_reason="output_leak",
                )
            logger.info("General answer rejected: %s", ",".join(validation.reasons))
            return AnswerResult(
                GENERAL_ERROR_ANSWER,
                AnswerKind.ERROR,
                blocked_reason="output_rejected",
            )
        return AnswerResult(validation.text, AnswerKind.GENERAL)

    async def _llm_answer(
        self,
        user_id: int,
        question: str,
        response: SearchResponse,
        best: SearchResult,
        live_status: str | None,
    ) -> AnswerResult | None:
        if self.llm is None:
            return None
        prompt = build_user_prompt(question, response.results, live_status=live_status)
        try:
            raw = await self.llm.complete(system=SYSTEM_PROMPT, user=prompt)
        except LLMError as exc:
            logger.warning("LLM failed: %s", type(exc).__name__)
            return None
        text = _strip_emoji(raw).strip()
        if not text:
            return None
        validation = validate_output(
            text,
            allowed_domains=self.settings.allowed_domains,
            max_chars=self.settings.max_answer_chars,
        )
        if not validation.ok:
            if set(validation.reasons) <= {"missing_source"}:
                text = _with_source(text, best)
                validation = validate_output(
                    text,
                    allowed_domains=self.settings.allowed_domains,
                    max_chars=self.settings.max_answer_chars,
                )
            if not validation.ok:
                if (
                    "secret_leak" in validation.reasons
                    or "internal_prompt_leak" in validation.reasons
                ):
                    await self._violation(user_id, "output_leak", ",".join(validation.reasons))
                    return AnswerResult(
                        PRIVATE_ANSWER, AnswerKind.REFUSAL, blocked_reason="output_leak"
                    )
                return None
        if validation.text.strip() == NO_FACT_ANSWER:
            return await self._no_fact(user_id, question, response, live_status)
        return AnswerResult(
            validation.text,
            AnswerKind.FACT,
            confidence=response.confidence,
            sources=[best.source_label or best.source_url],
        )

    def _donation_fact(self, question: str) -> str | None:
        """Answer exact price questions from the canonical package table only."""
        lowered = question.lower().replace("ё", "е")
        if "печат" not in lowered and "пасс" not in lowered and "pass" not in lowered:
            return None
        try:
            config = self.knowledge.donation_config()
        except Exception:
            return None
        packages = config.get("packages") or []
        package: dict[str, Any] | None = None
        if "пасс" in lowered or "pass" in lowered:
            package = next(
                (item for item in packages if item.get("id") == "game-pass"),
                None,
            )
        else:
            match = re.search(r"(\d{1,4})\s*(?:печат|печати|печать|печатей)", lowered)
            if match:
                seals = int(match.group(1))
                package = next(
                    (item for item in packages if int(item.get("seals") or 0) == seals),
                    None,
                )
        if package is None:
            return None
        label = str(package.get("label") or "Пакет")
        rub = f"{int(package.get('rub') or 0):,}".replace(",", " ")
        stars = f"{int(package.get('stars') or 0):,}".replace(",", " ")
        uzs = f"{int(package.get('uzs') or 0):,}".replace(",", " ")
        usd = str(package.get("usd") or "0.00")
        source = str(config.get("source_label") or "#Aetherlyn #donate")
        answer = (
            f"{label}: {rub} ₽ / {stars} звёзд / {usd} $ / {uzs} сум.\n"
            "Покупка — через раздел «Донаты», после подтверждения оплаты владельцем.\n"
            f"Источник: {source}"
        )
        return answer

    def _deterministic(self, result: SearchResult) -> str | None:
        source = result.answer or result.content
        if not source:
            return None
        answer = _with_source(source, result)
        lines: list[str] = []
        for line in answer.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            lines.append(stripped)
        if len(lines) > 6:
            # Never trim a fact table silently. A table that does not fit is not an answer.
            return None
        answer = "\n".join(lines)
        validation = validate_output(
            answer,
            allowed_domains=self.settings.allowed_domains,
            max_chars=self.settings.max_answer_chars,
        )
        return validation.text if validation.ok else None

    async def _no_fact(
        self,
        user_id: int,
        question: str,
        response: SearchResponse,
        live_status: str | None,
    ) -> AnswerResult:
        ticket_id: int | None = None
        if self.tickets is not None:
            ticket = await self.tickets.create(
                kind=TicketKind.QUESTION,
                user_id=user_id,
                subject=question[:120],
                payload={
                    "question": question[:900],
                    "username": None,
                    "source": "llm_no_fact",
                    "confidence": round(response.confidence, 3),
                },
            )
            ticket_id = ticket.id
        await self.learning.record_unanswered(
            user_id=user_id,
            question=question,
            source="question",
            ticket_id=ticket_id,
        )
        if ticket_id is not None:
            await self.audit.record(
                actor_id=user_id,
                action="unanswered_escalated",
                target=str(ticket_id),
                result="ok",
                details=f"confidence={response.confidence:.3f}",
            )
        return AnswerResult(
            NO_FACT_ANSWER,
            AnswerKind.NO_FACT,
            confidence=response.confidence,
            ticket_id=ticket_id,
            blocked_reason="no_fact",
        )

    async def _violation(self, user_id: int, action: str, reason: str) -> None:
        try:
            async with self.database.session() as session:
                user = await session.get(User, user_id)
                if user is None:
                    user = User(id=user_id, captcha_ok=True)
                    session.add(user)
                user.violations = (user.violations or 0) + 1
            await self.audit.record(
                actor_id=user_id,
                action=f"guard_block:{action}",
                target=str(user_id),
                result="blocked",
                details=reason[:200],
            )
        except Exception:
            logger.warning("Could not persist guard violation", exc_info=True)


__all__ = ["AnswerKind", "AnswerResult", "AssistantService", "wrap_user_data"]
