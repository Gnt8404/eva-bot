"""Server information, plugin/datapack catalogue and plugin search."""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    BTN_PLUGINS,
    BTN_SERVER,
    CB_PLUGINS_DATAPACKS,
    CB_PLUGINS_LIST,
    CB_PLUGINS_SEARCH,
    back_to_plugins,
    main_menu,
    plugins_menu,
)
from app.bot.states import PluginSearch
from app.config import Settings
from app.kb.store import KnowledgeBase
from app.server.status import StatusService

logger = logging.getLogger(__name__)
router = Router(name="menu")
PAGE_SIZE = 8
STATUS_UNAVAILABLE = "Статус сейчас недоступен — проверьте позже."


def _server_text(settings: Settings, status_text: str, channels: list[dict[str, Any]]) -> str:
    host = settings.server_host.strip()
    ip_line = f"IP: {host}" if host else "IP: не указан в базе знаний — уточните у владельца"
    lines = [
        "Aetherlyn, сезон 3.",
        f"{ip_line}, порт {settings.server_port}.",
        "Java Paper 1.21.11 (build 127), выживание/PvE, difficulty hard, до 20 игроков.",
        "Bedrock: через Geyser/Floodgate, вход по тому же адресу.",
        status_text,
        "Регистрация: /register, вход: /login. Гриферство и читы запрещены.",
    ]
    links = [f"{item.get('label')}: {item.get('url')}" for item in channels if item.get("url")]
    if links:
        lines.append("Каналы: " + " | ".join(links[:3]))
    lines.append("Источник: база знаний Aetherlyn (#Aetherlyn #server).")
    return "\n".join(lines)


@router.message(Command("server"))
@router.message(F.text == BTN_SERVER)
async def server_info(
    message: Message,
    settings: Settings,
    kb: KnowledgeBase,
    status: StatusService,
) -> None:
    try:
        snapshot = await asyncio.wait_for(status.snapshot(), timeout=8)
        status_text = snapshot.as_text()
    except Exception:
        logger.exception("server status unavailable")
        status_text = STATUS_UNAVAILABLE
    text = _server_text(settings, status_text, kb.channels())
    await message.answer(text, parse_mode=None)


def _can_see_admin(settings: Settings, user_id: int | None) -> bool:
    if user_id is None:
        return False
    return settings.is_owner(user_id) or settings.is_readonly_admin(user_id)


def _plugins_text(
    plugins: list[dict[str, Any]], page: int, *, include_admin: bool
) -> tuple[str, int]:
    pages = max(1, math.ceil(len(plugins) / PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    subset = plugins[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    access_note = (
        "Полный список: владелец/админ."
        if include_admin
        else "Показаны только плагины, доступные игрокам; административные скрыты."
    )
    lines = [
        f"Плагины Aetherlyn, страница {page + 1}/{pages}. {access_note} "
        "Источник: API сервера / база знаний."
    ]
    for item in subset:
        version = str(item.get("version") or "").strip()
        description = str(item.get("description") or "").strip()
        if len(description) > 70:
            description = description[:67] + "..."
        line = f"• {item.get('name')}" + (f" {version}" if version else "")
        if description:
            line += f" — {description}"
        lines.append(line)
    return "\n".join(lines), pages


async def _send_plugins(
    target: Message, kb: KnowledgeBase, page: int, *, include_admin: bool
) -> None:
    plugins = kb.plugins(include_admin=include_admin)
    text, pages = _plugins_text(plugins, page, include_admin=include_admin)
    await target.answer(text, reply_markup=plugins_menu(page, pages))


@router.message(Command("plugins"))
@router.message(F.text == BTN_PLUGINS)
async def plugins_entry(message: Message, kb: KnowledgeBase, settings: Settings) -> None:
    user_id = message.from_user.id if message.from_user else None
    await _send_plugins(
        message,
        kb,
        0,
        include_admin=_can_see_admin(settings, user_id),
    )


@router.callback_query(F.data.startswith(f"{CB_PLUGINS_LIST}:"))
async def plugins_page(callback: CallbackQuery, kb: KnowledgeBase, settings: Settings) -> None:
    raw = (callback.data or "").split(":")[-1]
    page = int(raw) if raw.isdigit() else 0
    user_id = callback.from_user.id if callback.from_user else None
    plugins = kb.plugins(include_admin=_can_see_admin(settings, user_id))
    text, pages = _plugins_text(plugins, page, include_admin=_can_see_admin(settings, user_id))
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=plugins_menu(page, pages))


@router.callback_query(F.data == CB_PLUGINS_DATAPACKS)
async def datapacks(callback: CallbackQuery, kb: KnowledgeBase) -> None:
    await callback.answer()
    lines = ["Датапаки мира Aetherlyn. Источник: API сервера / база знаний."]
    for item in kb.datapacks():
        description = str(item.get("description") or "").strip()
        lines.append(f"• {item.get('name')}" + (f" — {description}" if description else ""))
    if isinstance(callback.message, Message):
        await callback.message.edit_text("\n".join(lines), reply_markup=back_to_plugins())


@router.callback_query(F.data == CB_PLUGINS_SEARCH)
async def search_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PluginSearch.waiting)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите часть названия плагина или датапака.",
            reply_markup=back_to_plugins(),
        )


@router.message(PluginSearch.waiting)
async def search_plugins(
    message: Message,
    state: FSMContext,
    kb: KnowledgeBase,
    settings: Settings,
) -> None:
    query = (message.text or "").strip().lower()
    await state.clear()
    if not query:
        await message.answer(
            "Пустой запрос.",
            reply_markup=main_menu(
                settings.is_owner(message.from_user.id) if message.from_user else False
            ),
        )
        return
    found: list[str] = []
    user_id = message.from_user.id if message.from_user else None
    for item in kb.plugins(include_admin=_can_see_admin(settings, user_id)):
        haystack = " ".join(
            str(item.get(key) or "") for key in ("name", "description", "version")
        ).lower()
        if query in haystack:
            found.append(f"Плагин: {item.get('name')} {item.get('version') or ''}".strip())
    for item in kb.datapacks():
        haystack = " ".join(str(item.get(key) or "") for key in ("name", "description")).lower()
        if query in haystack:
            found.append(f"Датапак: {item.get('name')}")
    if not found:
        await message.answer(
            "Совпадений нет. Проверьте название или спросите иначе.",
            reply_markup=back_to_plugins(),
        )
        return
    await message.answer(
        "Найдено:\n" + "\n".join(found[:12]),
        reply_markup=back_to_plugins(),
    )
