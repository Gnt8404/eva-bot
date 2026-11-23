"""Validation for forms and user-supplied media."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from aiogram import Bot
from aiogram.types import Message

from app.config import Settings
from app.llm.guard import sanitize_input

NICK_RE = re.compile(r"^[^\s<>]{2,32}$")
TG_RE = re.compile(r"^@?[A-Za-z0-9_]{4,32}$")
URL_RE = re.compile(r"^https?://[^\s<>]{4,300}$", re.IGNORECASE)
BAD_FILE_EXT = {
    ".exe",
    ".bat",
    ".cmd",
    ".com",
    ".msi",
    ".ps1",
    ".sh",
    ".js",
    ".vbs",
    ".scr",
    ".jar",
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
}
IMAGE_MIME = {"image/jpeg", "image/png", "image/webp"}


class FormError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValidatedMedia:
    file_id: str
    kind: str
    content_type: str
    size: int


def text_value(raw: str, *, field: str, minimum: int = 2, maximum: int = 500) -> str:
    clean = sanitize_input(raw, maximum)
    if len(clean) < minimum:
        raise FormError(f"{field}: слишком коротко")
    return clean


def nickname(raw: str) -> str:
    clean = text_value(raw, field="Ник", minimum=2, maximum=32)
    if not NICK_RE.match(clean):
        raise FormError("Ник: недопустимые символы или пробелы")
    return clean


def telegram_username(raw: str) -> str:
    clean = text_value(raw, field="Telegram", minimum=4, maximum=33).lstrip("@")
    if not TG_RE.match(clean):
        raise FormError("Telegram: укажите юзернейм вида @name")
    return clean


def age_value(raw: str) -> int:
    clean = text_value(raw, field="Возраст", minimum=1, maximum=3)
    if not clean.isdigit():
        raise FormError("Возраст: нужно число")
    value = int(clean)
    if value < 10 or value > 99:
        raise FormError("Возраст: допустимо 10–99")
    return value


def hours_value(raw: str) -> int:
    clean = text_value(raw, field="Часы", minimum=1, maximum=3)
    if not clean.isdigit():
        raise FormError("Часы: нужно число")
    value = int(clean)
    if value < 0 or value > 168:
        raise FormError("Часы: допустимо 0–168")
    return value


def date_value(raw: str) -> str:
    clean = text_value(raw, field="Дата", minimum=3, maximum=80)
    if not re.search(r"\d", clean):
        raise FormError("Дата: укажите дату и время цифрами")
    return clean


def portfolio_value(raw: str) -> str:
    clean = text_value(raw, field="Портфолио", minimum=1, maximum=300)
    if clean in {"-", "нет", "Нет"}:
        return "—"
    if not URL_RE.match(clean):
        raise FormError("Портфолио: нужна ссылка http(s) или «-»")
    return clean


def image_magic(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def _download(bot: Bot, file_id: str) -> bytes:
    buffer = io.BytesIO()
    await bot.download(file_id, destination=buffer)
    return buffer.getvalue()


async def validate_image(
    message: Message,
    bot: Bot,
    settings: Settings,
) -> ValidatedMedia:
    if message.photo:
        photo = message.photo[-1]
        if photo.file_size and photo.file_size > settings.max_upload_bytes:
            raise FormError("Файл больше 20 МБ")
        data = await _download(bot, photo.file_id)
        if image_magic(data) is None:
            raise FormError("Нужен JPEG, PNG или WEBP")
        return ValidatedMedia(photo.file_id, "photo", "image/jpeg", len(data))
    document = message.document
    if document is not None:
        mime = (document.mime_type or "").lower()
        name = (document.file_name or "").lower()
        if mime not in IMAGE_MIME:
            raise FormError("Скриншот: только JPEG, PNG или WEBP")
        if any(name.endswith(ext) for ext in BAD_FILE_EXT):
            raise FormError("Исполняемые файлы и архивы запрещены")
        if document.file_size and document.file_size > settings.max_upload_bytes:
            raise FormError("Файл больше 20 МБ")
        data = await _download(bot, document.file_id)
        if image_magic(data) is None:
            raise FormError("Содержимое не является изображением")
        return ValidatedMedia(document.file_id, "document", mime, len(data))
    raise FormError("Пришлите фото или файл-изображение")


async def validate_report_evidence(
    message: Message,
    bot: Bot,
    settings: Settings,
) -> ValidatedMedia:
    if message.video is not None:
        video = message.video
        if video.file_size and video.file_size > settings.max_upload_bytes:
            raise FormError("Видео больше 20 МБ")
        mime = (video.mime_type or "video/mp4").lower()
        if not mime.startswith("video/"):
            raise FormError("Видео: неподдерживаемый формат")
        return ValidatedMedia(video.file_id, "video", mime, video.file_size or 0)
    return await validate_image(message, bot, settings)
