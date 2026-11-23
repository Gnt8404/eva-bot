"""All reply/inline keyboards and callback payloads in one place."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

BTN_HELP = "Нужна помощь"
BTN_REPORT = "Жалоба на игрока"
BTN_SERVER = "О сервере"
BTN_PLUGINS = "Плагины и датапаки"
BTN_APPLY = "Анкета"
BTN_DONATE = "Донаты"
BTN_CONSOLE = "Консоль"

CB_CAPTCHA = "captcha:ok"
CB_CHECK_SUB = "subscription:check"
CB_CANCEL = "flow:cancel"
CB_HELP_ADMIN = "help:admin"
CB_REPORT_START = "report:start"
CB_APPLY_START = "apply:start"
CB_DONATE_PAID = "donate:paid"
CB_DONATE_REQUISITES = "donate:req"
CB_PLUGINS_LIST = "plugins:list"
CB_PLUGINS_DATAPACKS = "plugins:datapacks"
CB_PLUGINS_SEARCH = "plugins:search"
CB_CONSOLE = "console:open"
CB_ADMIN = "admin:open"


def main_menu(is_owner: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_HELP), KeyboardButton(text=BTN_REPORT)],
        [KeyboardButton(text=BTN_SERVER), KeyboardButton(text=BTN_PLUGINS)],
        [KeyboardButton(text=BTN_APPLY), KeyboardButton(text=BTN_DONATE)],
    ]
    if is_owner:
        rows.append([KeyboardButton(text=BTN_CONSOLE)])
    return ReplyKeyboardMarkup(
        keyboard=rows, resize_keyboard=True, input_field_placeholder="Вопрос о сервере Aetherlyn"
    )


def captcha_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердите, что вы человек", callback_data=CB_CAPTCHA)]
        ]
    )


def subscription_menu(url: str) -> InlineKeyboardMarkup:
    rows = []
    if url:
        rows.append([InlineKeyboardButton(text="Открыть канал", url=url)])
    rows.append([InlineKeyboardButton(text="Я подписался", callback_data=CB_CHECK_SUB)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=CB_CANCEL)]]
    )


def help_menu() -> InlineKeyboardMarkup:
    topics = [
        ("Вход / логин", "login"),
        ("Лаги", "lag"),
        ("Потеря вещей", "lost"),
        ("Гриферство", "grief"),
        ("Sorrow", "sorrow"),
        ("Донат", "donate"),
        ("Другое", "other"),
    ]
    rows = [
        [InlineKeyboardButton(text=title, callback_data=f"help:topic:{key}")]
        for title, key in topics
    ]
    rows.append([InlineKeyboardButton(text="Позвать администратора", callback_data=CB_HELP_ADMIN)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def escalation_menu(topic: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Позвать администратора", callback_data=f"help:escalate:{topic}"
                )
            ],
            [InlineKeyboardButton(text="В меню помощи", callback_data="help:topic:other")],
        ]
    )


def report_confirm_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить отправку", callback_data="report:confirm")],
            [InlineKeyboardButton(text="Отмена", callback_data=CB_CANCEL)],
        ]
    )


def apply_mic_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Есть", callback_data="apply:mic:yes"),
                InlineKeyboardButton(text="Нет", callback_data="apply:mic:no"),
            ],
            [InlineKeyboardButton(text="Отмена", callback_data=CB_CANCEL)],
        ]
    )


def apply_confirm_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отправить анкету", callback_data="apply:confirm")],
            [InlineKeyboardButton(text="Отмена", callback_data=CB_CANCEL)],
        ]
    )


def donate_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Я оплатил", callback_data=CB_DONATE_PAID)],
            [InlineKeyboardButton(text="Показать реквизиты", callback_data=CB_DONATE_REQUISITES)],
        ]
    )


def currency_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Рубли", callback_data="donate:req:RUB"),
                InlineKeyboardButton(text="Доллары", callback_data="donate:req:USD"),
                InlineKeyboardButton(text="Сумы", callback_data="donate:req:UZS"),
            ],
            [InlineKeyboardButton(text="Назад", callback_data="donate:menu")],
        ]
    )


def packages_menu(packages: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for package_id, label in packages:
        row.append(InlineKeyboardButton(text=label, callback_data=f"donate:pkg:{package_id}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=CB_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plugins_menu(page: int, pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="Назад", callback_data=f"{CB_PLUGINS_LIST}:{page - 1}")
        )
    if page + 1 < pages:
        nav.append(
            InlineKeyboardButton(text="Далее", callback_data=f"{CB_PLUGINS_LIST}:{page + 1}")
        )
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(text="Датапаки", callback_data=CB_PLUGINS_DATAPACKS),
            InlineKeyboardButton(text="Поиск", callback_data=CB_PLUGINS_SEARCH),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_plugins() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Плагины", callback_data=f"{CB_PLUGINS_LIST}:0"),
                InlineKeyboardButton(text="Датапаки", callback_data=CB_PLUGINS_DATAPACKS),
            ]
        ]
    )


def console_commands(commands: list[tuple[int, str, bool]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for index, title, destructive in commands:
        prefix = "! " if destructive else ""
        row.append(
            InlineKeyboardButton(text=f"{prefix}{title}", callback_data=f"console:cmd:{index}")
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Закрыть", callback_data="console:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_console(token: str, command: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить", callback_data=f"console:confirm:{token}")],
            [InlineKeyboardButton(text="Отмена", callback_data=f"console:cancel:{token}")],
        ]
    )


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Статистика", callback_data="admin:stats"),
                InlineKeyboardButton(text="Тикеты", callback_data="admin:tickets"),
            ],
            [
                InlineKeyboardButton(text="Реиндекс базы", callback_data="admin:reindex"),
                InlineKeyboardButton(text="Последний лог", callback_data="admin:log"),
            ],
            [
                InlineKeyboardButton(text="Бэкап", callback_data="admin:backup"),
                InlineKeyboardButton(text="Рассылка", callback_data="admin:broadcast"),
            ],
        ]
    )


def report_decision(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Принять", callback_data=f"ticket:report:{ticket_id}:approve"
                ),
                InlineKeyboardButton(
                    text="Отклонить", callback_data=f"ticket:report:{ticket_id}:reject"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Запросить детали", callback_data=f"ticket:report:{ticket_id}:details"
                )
            ],
        ]
    )


def application_decision(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Взять на испытательный", callback_data=f"ticket:app:{ticket_id}:probation"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отказать", callback_data=f"ticket:app:{ticket_id}:reject"
                ),
                InlineKeyboardButton(
                    text="В резерв", callback_data=f"ticket:app:{ticket_id}:reserve"
                ),
            ],
        ]
    )


def donation_decision(donation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить", callback_data=f"ticket:don:{donation_id}:confirm"
                ),
                InlineKeyboardButton(
                    text="Отклонить", callback_data=f"ticket:don:{donation_id}:reject"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Запросить чек ещё раз", callback_data=f"ticket:don:{donation_id}:receipt"
                )
            ],
        ]
    )


def question_decision(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Ответить", callback_data=f"faq:answer:{ticket_id}")]
        ]
    )


def broadcast_confirm(count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Отправить {count} пользователям", callback_data="admin:broadcast:confirm"
                )
            ],
            [InlineKeyboardButton(text="Отмена", callback_data=CB_CANCEL)],
        ]
    )


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Главное меню", callback_data="menu:main")]]
    )


__all__ = [name for name in dir() if name.startswith(("BTN_", "CB_"))] + [
    "admin_menu",
    "application_decision",
    "apply_confirm_menu",
    "apply_mic_menu",
    "back_menu",
    "back_to_plugins",
    "broadcast_confirm",
    "cancel_menu",
    "captcha_menu",
    "confirm_console",
    "console_commands",
    "currency_menu",
    "donate_menu",
    "donation_decision",
    "escalation_menu",
    "help_menu",
    "main_menu",
    "packages_menu",
    "plugins_menu",
    "question_decision",
    "report_confirm_menu",
    "report_decision",
    "subscription_menu",
]
