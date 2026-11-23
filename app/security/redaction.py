"""Redaction of secrets and payment card numbers in logs, alerts and audit records."""

from __future__ import annotations

import re

# Values registered at startup (bot token, API keys and similar). They are never logged.
_KNOWN_SECRETS: set[str] = set()

_BOT_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{25,}\b")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_PTERO_KEY_RE = re.compile(r"\bptlc_[A-Za-z0-9_-]{6,}\b")
_HEX_SECRET_RE = re.compile(r"\b[A-Fa-f0-9]{32,}\b")
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,18}\d(?!\d)")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_TELEGRAM_SESSION_RE = re.compile(r"\b1[A-Za-z0-9+/=_-]{40,}\b")


def register_secret(value: str | None) -> None:
    if value:
        stripped = value.strip()
        if len(stripped) >= 4:
            _KNOWN_SECRETS.add(stripped)


def clear_registered_secrets() -> None:
    _KNOWN_SECRETS.clear()


def redact_text(text: str, *, mask_cards: bool = True) -> str:
    if not text:
        return text
    result = str(text)
    for secret in sorted(_KNOWN_SECRETS, key=len, reverse=True):
        if secret and secret in result:
            result = result.replace(secret, "****")
    result = _BOT_TOKEN_RE.sub("bot********:****", result)
    result = _OPENAI_KEY_RE.sub("sk-****", result)
    result = _PTERO_KEY_RE.sub("ptlc_****", result)
    result = _JWT_RE.sub("****", result)
    result = _TELEGRAM_SESSION_RE.sub("****", result)
    result = _HEX_SECRET_RE.sub("****", result)
    if mask_cards:
        result = _CARD_RE.sub("**** **** **** ****", result)
    return result


def mask_card(card: str) -> str:
    digits = re.sub(r"\D", "", card)
    if len(digits) < 4:
        return "****"
    return "**** **** **** " + digits[-4:]


# Backwards-compatible short alias used by the knowledge-base layer.
redact = redact_text
