"""Minimal OpenAI-compatible chat client with no secret logging."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import Settings
from app.security.redaction import redact_text

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.llm_ready

    @property
    def endpoint(self) -> str:
        base = self.settings.llm_base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.llm_timeout_seconds),
                headers={
                    "Authorization": f"Bearer {self.settings.llm_api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def complete(self, *, system: str, user: str) -> str:
        if not self.enabled:
            raise LLMError("LLM is not configured")
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
            "max_tokens": self.settings.llm_max_tokens,
        }
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self._http().post(self.endpoint, json=payload)
                if response.status_code >= 500 or response.status_code == 429:
                    raise LLMError(f"LLM upstream status {response.status_code}")
                if response.status_code >= 400:
                    body = redact_text(response.text)[:300]
                    raise LLMError(f"LLM request failed with {response.status_code}: {body}")
                data: dict[str, Any] = response.json()
                choices = data.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise LLMError("LLM response has no choices")
                message = choices[0].get("message", {})
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise LLMError("LLM response is empty")
                return content.strip()
            except (httpx.HTTPError, ValueError, LLMError) as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(1.0)
                    continue
        logger.warning("LLM request failed: %s", type(last_error).__name__)
        raise LLMError("LLM unavailable") from last_error
