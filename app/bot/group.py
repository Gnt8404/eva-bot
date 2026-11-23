"""Group-chat addressing helpers.

Eva answers every private message, but in groups she must not interrupt every
conversation. A group message is addressed to her when it mentions the bot by
username, replies to one of her messages, or contains her name ("Ева", "Еву",
"Eva", ...). The owner can opt into answering every group message with
``GROUP_REPLY_ALL=true``.
"""

from __future__ import annotations

import re
from typing import Final

from aiogram.types import Message

GROUP_CHAT_TYPES: Final = frozenset({"group", "supergroup"})

# Word-bounded forms of the bot's name. The lookarounds avoid matching
# "Евангелие", "Европа", "Евгений" and similar words.
_NAME_RE: Final = re.compile(
    r"(?<![а-яёa-z])"
    r"(?:ева|еву|еве|евой|евою|эва|эву|эве|эвой|эвою|eva)"
    r"(?![а-яёa-z])",
    re.IGNORECASE,
)


def is_group(message: Message) -> bool:
    """Return True for a group or supergroup message."""
    chat_type = getattr(message.chat.type, "value", message.chat.type)
    return str(chat_type) in GROUP_CHAT_TYPES


def contains_name(text: str | None) -> bool:
    """Return True when text contains a standalone form of the bot's name."""
    if not text:
        return False
    return bool(_NAME_RE.search(text))


def _bot_username(username: str) -> str:
    return username.strip().lstrip("@").casefold()


def _mentions_bot(text: str | None, username: str) -> bool:
    if not text or not username:
        return False
    mention = f"@{_bot_username(username)}"
    return mention in text.casefold()


def _replies_to_bot(message: Message, username: str) -> bool:
    reply = message.reply_to_message
    if reply is None or reply.from_user is None:
        return False
    if not reply.from_user.is_bot:
        return False
    if not username:
        return False
    return (reply.from_user.username or "").casefold() == _bot_username(username)


def is_addressed(
    message: Message,
    bot_username: str,
    *,
    reply_all: bool = False,
) -> bool:
    """Return True when a group message is meant for Eva.

    Private messages always count as addressed. In groups the default trigger
    is a username mention, a reply to Eva, or her name in the text/caption.
    """
    if not is_group(message):
        return True
    if reply_all:
        return True
    if _mentions_bot(message.text, bot_username):
        return True
    if _mentions_bot(message.caption, bot_username):
        return True
    if _replies_to_bot(message, bot_username):
        return True
    return contains_name(message.text) or contains_name(message.caption)


def clean_question(text: str, bot_username: str) -> str:
    """Remove a leading @mention or name from the question, keeping the rest."""
    value = text.strip()
    username = _bot_username(bot_username)
    if username and value.casefold().startswith(f"@{username}"):
        value = value[1 + len(username) :].lstrip(" ,:!-—")
    value = re.sub(
        r"^(?:ева|еву|еве|евой|эва|эву|эве|эвой|eva)\s*[,:!\-—]*\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return value.strip()
