"""Group-chat addressing rules."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.bot.group import clean_question, contains_name, is_addressed, is_group


def _message(
    text: str | None = None,
    *,
    caption: str | None = None,
    chat_type: str = "supergroup",
    reply_username: str | None = None,
    reply_is_bot: bool = True,
) -> SimpleNamespace:
    reply = None
    if reply_username is not None:
        reply = SimpleNamespace(
            from_user=SimpleNamespace(
                is_bot=reply_is_bot,
                username=reply_username,
            )
        )
    return SimpleNamespace(
        text=text,
        caption=caption,
        chat=SimpleNamespace(type=chat_type),
        reply_to_message=reply,
    )


def test_group_detection() -> None:
    assert is_group(_message("привет"))
    assert not is_group(_message("привет", chat_type="private"))


@pytest.mark.parametrize(
    "text",
    [
        "Ева, какие правила?",
        "ева привет",
        "Еву позовите",
        "Спросите Еве",
        "Евой довольны",
        "EVA, help",
        "эй ева",
    ],
)
def test_name_forms_are_addressed(text: str) -> None:
    assert contains_name(text)
    assert is_addressed(_message(text), "Aetherlynbot")


@pytest.mark.parametrize("text", ["Евангелие", "Европа", "Евгений", "привет всем", "Evan"])
def test_similar_words_are_not_addressed(text: str) -> None:
    assert not contains_name(text)
    assert not is_addressed(_message(text), "Aetherlynbot")


def test_mention_and_reply_are_addressed() -> None:
    assert is_addressed(_message("@Aetherlynbot, что по донату?"), "Aetherlynbot")
    assert is_addressed(
        _message("что по донату?", reply_username="Aetherlynbot"),
        "Aetherlynbot",
    )
    assert not is_addressed(
        _message("что по донату?", reply_username="Aetherlynbot", reply_is_bot=False),
        "Aetherlynbot",
    )


def test_reply_all_override() -> None:
    assert is_addressed(_message("просто сообщение"), "Aetherlynbot", reply_all=True)
    assert not is_addressed(_message("просто сообщение"), "Aetherlynbot")


def test_private_always_addressed() -> None:
    assert is_addressed(_message("любой текст", chat_type="private"), "Aetherlynbot")


def test_clean_question() -> None:
    assert clean_question("@Aetherlynbot, какие правила?", "Aetherlynbot") == "какие правила?"
    assert clean_question("Ева, какие правила?", "Aetherlynbot") == "какие правила?"
    assert clean_question("Еву: что по донату", "Aetherlynbot") == "что по донату"
    assert clean_question("просто вопрос", "Aetherlynbot") == "просто вопрос"


def test_caption_can_address_bot() -> None:
    message = _message(caption="Ева, посмотри скрин", text=None)
    assert is_addressed(message, "Aetherlynbot")
