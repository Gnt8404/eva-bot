"""Donation table, requisites and four-step purchase application."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    BTN_DONATE,
    CB_DONATE_PAID,
    CB_DONATE_REQUISITES,
    cancel_menu,
    currency_menu,
    donate_menu,
    packages_menu,
)
from app.bot.media import FormError, text_value, validate_image
from app.bot.states import DonationForm
from app.config import Settings
from app.notify import Notifier
from app.tickets.cards import donation_card
from app.tickets.model import Currency
from app.tickets.service import DonationService

router = Router(name="donate")


async def _send_donation(message: Message, donations: DonationService) -> None:
    await message.answer(donations.message, reply_markup=donate_menu())
    await message.answer(
        "Выберите пакет в заявке после оплаты. Промокод выдаётся только после проверки чека владельцем.",
    )


@router.message(F.text == BTN_DONATE)
async def donate_entry(message: Message, donations: DonationService) -> None:
    await _send_donation(message, donations)


@router.callback_query(F.data == "donate:menu")
async def donate_callback(callback: CallbackQuery, donations: DonationService) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await _send_donation(callback.message, donations)


@router.callback_query(F.data == CB_DONATE_REQUISITES)
async def requisites_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Выберите валюту. Полный номер карты показывается только в следующем сообщении.",
            reply_markup=currency_menu(),
        )


@router.callback_query(F.data.startswith("donate:req:"))
async def show_requisites(callback: CallbackQuery, donations: DonationService) -> None:
    raw = (callback.data or "").split(":")[-1]
    try:
        currency = Currency(raw)
    except ValueError:
        await callback.answer("Неизвестная валюта", show_alert=True)
        return
    item = donations.requisites(currency)
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    await callback.message.answer(
        f"Реквизиты для {currency.value}:\n"
        f"Полный номер карты: {item.full_card}\n"
        f"Последние 4 цифры: {item.last4}\n"
        "Сумму берите из выбранного пакета. После оплаты нажмите «Донаты» → «Я оплатил».",
    )
    # The full number is intentionally sent only here and is never logged or stored in a ticket.
    await callback.message.answer("Выберите действие:", reply_markup=donate_menu())


@router.callback_query(F.data == CB_DONATE_PAID)
async def start_purchase(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(DonationForm.nickname)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer("Шаг 1/4. Ваш ник в игре.", reply_markup=cancel_menu())


@router.message(DonationForm.nickname)
async def step_nickname(message: Message, state: FSMContext) -> None:
    try:
        value = text_value(message.text or "", field="Ник", minimum=2, maximum=32)
    except FormError as exc:
        await message.answer(f"{exc}. Попробуйте ещё раз.")
        return
    await state.update_data(nickname=value)
    await state.set_state(DonationForm.telegram)
    await message.answer(
        "Шаг 2/4. Ваш постоянный ник или юзернейм в Telegram.",
        reply_markup=cancel_menu(),
    )


@router.message(DonationForm.telegram)
async def step_telegram(message: Message, state: FSMContext) -> None:
    try:
        value = text_value(message.text or "", field="Telegram", minimum=2, maximum=64)
    except FormError as exc:
        await message.answer(f"{exc}. Попробуйте ещё раз.")
        return
    await state.update_data(telegram=value)
    await state.set_state(DonationForm.screenshot)
    await message.answer(
        "Шаг 3/4. Пришлите скриншот чека (JPEG/PNG/WEBP, до 20 МБ).",
        reply_markup=cancel_menu(),
    )


@router.message(DonationForm.screenshot)
async def step_screenshot(
    message: Message,
    state: FSMContext,
    bot: Bot,
    settings: Settings,
    donations: DonationService,
) -> None:
    try:
        media = await validate_image(message, bot, settings)
    except FormError as exc:
        await message.answer(f"{exc}. Пришлите чек ещё раз.")
        return
    await state.update_data(
        screenshot_file_id=media.file_id,
        screenshot_kind=media.kind,
    )
    await state.set_state(DonationForm.package)
    data = await state.get_data()
    labels = [(item.id, f"{item.label} — {item.rub} ₽") for item in donations.packages]
    await message.answer(
        f"Шаг 4/4. Выберите пакет. Ник: {data.get('nickname')}.",
        reply_markup=packages_menu(labels),
    )


@router.callback_query(DonationForm.package, F.data.startswith("donate:pkg:"))
async def choose_package(
    callback: CallbackQuery,
    state: FSMContext,
    donations: DonationService,
    notifier: Notifier,
    settings: Settings,
) -> None:
    package_id = (callback.data or "").split(":")[-1]
    data = await state.get_data()
    if not data.get("nickname") or not data.get("screenshot_file_id"):
        await callback.answer("Заявка устарела. Начните заново.", show_alert=True)
        await state.clear()
        return
    if callback.from_user is None:
        await callback.answer()
        return
    try:
        package = donations.package(package_id)
    except ValueError:
        await callback.answer("Пакет не найден", show_alert=True)
        return
    await state.clear()
    ticket, donation = await donations.create_application(
        user_id=callback.from_user.id,
        username=str(data.get("telegram") or callback.from_user.username or ""),
        nickname=str(data.get("nickname")),
        package_id=package.id,
        screenshot_file_id=str(data.get("screenshot_file_id")),
        screenshot_kind=str(data.get("screenshot_kind") or "photo"),
    )
    card = donation_card(donation, ticket_id=ticket.id)
    from app.bot.keyboards import donation_decision

    mark = donation_decision(donation.id)
    await notifier.owner_attachment(
        card,
        file_id=str(data.get("screenshot_file_id")),
        kind=str(data.get("screenshot_kind") or "photo"),
        markup=mark,
    )
    await notifier.team(card, markup=None)
    await callback.answer("Заявка отправлена")
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Заявка #{donation.id} отправлена. Сумма: {package.rub} ₽ / "
            f"{package.usd} $ / {package.uzs} сум. Промокод выдаётся только после проверки чека.",
        )


@router.callback_query(F.data.startswith("donate:newreceipt:"))
async def new_receipt_request(
    callback: CallbackQuery,
    state: FSMContext,
    donations: DonationService,
) -> None:
    raw = (callback.data or "").split(":")[-1]
    if not raw.isdigit() or callback.from_user is None:
        await callback.answer("Некорректная заявка", show_alert=True)
        return
    donation = await donations.get(int(raw))
    if donation is None or donation.user_id != callback.from_user.id:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await state.set_state(DonationForm.new_receipt)
    await state.update_data(donation_id=donation.id)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Пришлите новый скриншот чека одним сообщением.",
            reply_markup=cancel_menu(),
        )


@router.message(DonationForm.new_receipt)
async def new_receipt(
    message: Message,
    state: FSMContext,
    bot: Bot,
    settings: Settings,
    donations: DonationService,
    notifier: Notifier,
) -> None:
    data = await state.get_data()
    donation_id = int(data.get("donation_id") or 0)
    if not donation_id:
        await state.clear()
        await message.answer("Заявка не найдена. Начните заново.")
        return
    try:
        media = await validate_image(message, bot, settings)
    except FormError as exc:
        await message.answer(f"{exc}. Пришлите чек ещё раз.")
        return
    donation = await donations.receive_new_receipt(
        donation_id, file_id=media.file_id, kind=media.kind
    )
    await state.clear()
    from app.bot.keyboards import donation_decision

    card = donation_card(donation, ticket_id=donation.ticket_id)
    await notifier.owner_attachment(
        card,
        file_id=media.file_id,
        kind=media.kind,
        markup=donation_decision(donation.id),
    )
    await message.answer(f"Новый чек по заявке #{donation.id} отправлен владельцу.")
