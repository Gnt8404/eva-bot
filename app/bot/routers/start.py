"""Entry point, captcha and subscription gate."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.group import is_group
from app.bot.keyboards import (
    CB_CAPTCHA,
    CB_CHECK_SUB,
    captcha_menu,
    main_menu,
    subscription_menu,
)
from app.config import Settings
from app.db.models import User
from app.db.session import Database

logger = logging.getLogger(__name__)
router = Router(name="start")

WELCOME = (
    "Ева — помощник сервера Aetherlyn.\n"
    "Выберите раздел кнопками ниже или откройте /menu.\n"
    "Анкета в команду и заявка в белый список — разные кнопки.\n"
    "Ответы только по фактам базы знаний, без выдумок."
)

SUBSCRIBED = {"member", "administrator", "creator"}


async def _subscribed(bot: Bot, settings: Settings, user_id: int) -> bool:
    if not settings.required_channel_id:
        return True
    try:
        member = await bot.get_chat_member(settings.required_channel_id, user_id)
    except Exception as exc:
        logger.warning("channel subscription check unavailable: %s", type(exc).__name__)
        return True
    return str(member.status) in SUBSCRIBED


async def _mark_captcha(database: Database, user_id: int) -> None:
    async with database.session() as session:
        user = await session.get(User, user_id)
        if user is not None:
            user.captcha_ok = True


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    settings: Settings,
    db_user: User,
    bot: Bot,
    database: Database,
) -> None:
    await state.clear()
    if is_group(message):
        if message.from_user is not None:
            await message.answer(
                "Меню Aetherlyn: выберите раздел кнопками ниже или откройте /menu.",
                reply_markup=main_menu(settings.is_owner(message.from_user.id)),
            )
        return
    if not db_user.captcha_ok and message.from_user and message.from_user.id != settings.owner_id:
        await message.answer(
            "Подтвердите, что вы человек, чтобы пользоваться ботом.",
            reply_markup=captcha_menu(),
        )
        return
    if message.from_user and not await _subscribed(bot, settings, message.from_user.id):
        await message.answer(
            "Для доступа подпишитесь на канал Aetherlyn и нажмите «Проверить подписку».",
            reply_markup=subscription_menu(
                settings.required_channel_url or "https://t.me/+utbI0nXnzAhmMWYy"
            ),
        )
        return
    if message.from_user is not None:
        await _mark_captcha(database, message.from_user.id)
        await message.answer(
            WELCOME, reply_markup=main_menu(settings.is_owner(message.from_user.id))
        )
    else:
        await message.answer(WELCOME)


@router.callback_query(F.data == CB_CAPTCHA)
async def captcha_ok(
    callback: CallbackQuery,
    settings: Settings,
    database: Database,
    bot: Bot,
) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    await _mark_captcha(database, callback.from_user.id)
    if not await _subscribed(bot, settings, callback.from_user.id):
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                "Осталось подписаться на канал Aetherlyn.",
                reply_markup=subscription_menu(
                    settings.required_channel_url or "https://t.me/+utbI0nXnzAhmMWYy"
                ),
            )
        return
    await callback.answer("Подтверждено")
    if isinstance(callback.message, Message):
        await callback.message.answer(
            WELCOME, reply_markup=main_menu(settings.is_owner(callback.from_user.id))
        )


@router.callback_query(F.data == CB_CHECK_SUB)
async def check_subscription(
    callback: CallbackQuery,
    settings: Settings,
    database: Database,
    bot: Bot,
) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    if not await _subscribed(bot, settings, callback.from_user.id):
        await callback.answer("Подписка пока не найдена.", show_alert=True)
        return
    await _mark_captcha(database, callback.from_user.id)
    await callback.answer("Подписка подтверждена")
    if isinstance(callback.message, Message):
        await callback.message.answer(
            WELCOME, reply_markup=main_menu(settings.is_owner(callback.from_user.id))
        )


@router.message(Command("menu"))
@router.callback_query(F.data == "menu:main")
async def show_menu(event: Message | CallbackQuery, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    markup = main_menu(settings.is_owner(event.from_user.id) if event.from_user else False)
    if isinstance(event, CallbackQuery):
        await event.answer()
        if isinstance(event.message, Message):
            await event.message.answer("Главное меню Aetherlyn.", reply_markup=markup)
    else:
        await event.answer("Главное меню Aetherlyn.", reply_markup=markup)


@router.message(Command("help"))
async def cmd_help(message: Message, settings: Settings) -> None:
    user_id = message.from_user.id if message.from_user else 0
    await message.answer(
        "Команды: /start — меню, /menu — главное меню, /server — о сервере, "
        "/plugins — плагины, /donate — донаты, /report — жалоба, "
        "/apply — анкета в команду, /whitelist — заявка в белый список, "
        "/back — как вернуться к месту смерти, /cancel — отменить действие.\n"
        "В группе команды и меню работают так же; для возврата к месту смерти "
        "используйте игровую команду /back, если она доступна.",
        reply_markup=main_menu(settings.is_owner(user_id)),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    user_id = message.from_user.id if message.from_user else 0
    await message.answer("Действие отменено.", reply_markup=main_menu(settings.is_owner(user_id)))


@router.callback_query(F.data == "flow:cancel")
async def cancel_flow(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await callback.answer("Отменено")
    if isinstance(callback.message, Message) and callback.from_user:
        await callback.message.answer(
            "Действие отменено.",
            reply_markup=main_menu(settings.is_owner(callback.from_user.id)),
        )
