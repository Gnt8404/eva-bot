"""Eva entry point: dependency wiring, webhook/health server and polling fallback."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import TelegramObject
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from app.bot.middlewares import (
    AccessMiddleware,
    I18nMiddleware,
    LoggingMiddleware,
    ThrottlingMiddleware,
)
from app.bot.routers import admin, ai, apply, donate, help, menu, report, start
from app.config import Settings, get_settings
from app.db.session import Database
from app.kb.learn import LearningService
from app.kb.store import KnowledgeBase
from app.llm.client import LLMClient
from app.llm.service import AssistantService
from app.logging_setup import configure_logging
from app.notify import Notifier
from app.security.audit import AuditService
from app.server.assets import AssetService
from app.server.commands import ConsoleService
from app.server.mcrcon import RconClient
from app.server.pterodactyl import PterodactylClient
from app.server.status import StatusService
from app.tickets.service import DonationService, TicketService
from app.workers.digest import DigestService
from app.workers.scheduler import Workers

logger = logging.getLogger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class ServicesMiddleware(BaseMiddleware):
    def __init__(self, services: dict[str, Any]) -> None:
        self.services = services

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data.update(self.services)
        return await handler(event, data)


def build_dispatcher(services: dict[str, Any], settings: Settings) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.outer_middleware(ServicesMiddleware(services))
    dispatcher.update.outer_middleware(LoggingMiddleware())
    dispatcher.update.outer_middleware(I18nMiddleware())
    for observer in (dispatcher.message, dispatcher.callback_query):
        observer.middleware(ThrottlingMiddleware())
        observer.middleware(AccessMiddleware())
    dispatcher.include_router(start.router)
    dispatcher.include_router(menu.router)
    dispatcher.include_router(help.router)
    dispatcher.include_router(report.router)
    dispatcher.include_router(apply.router)
    dispatcher.include_router(donate.router)
    dispatcher.include_router(admin.router)
    dispatcher.include_router(ai.router)
    return dispatcher


async def run() -> None:
    settings = get_settings()
    configure_logging(settings)
    if not settings.token:
        raise SystemExit(
            "BOT_TOKEN не задан. Скопируйте .env.example в .env и заполните BOT_TOKEN."
        )
    database = Database.from_settings(settings)
    await database.init_models()
    bot = Bot(
        token=settings.token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )
    panel = PterodactylClient(settings) if settings.panel_ready else None
    rcon = (
        RconClient(
            settings.rcon_host,
            settings.rcon_port,
            settings.rcon_password.get_secret_value(),
        )
        if settings.rcon_ready
        else None
    )
    audit = AuditService(database)
    kb = KnowledgeBase(database, settings)
    tickets = TicketService(database, settings, audit)
    notifications = Notifier(bot, settings)
    donations = DonationService(database, settings, tickets, audit)
    console = ConsoleService(settings, audit, rcon=rcon, panel=panel)
    status = StatusService(settings, panel, rcon)
    learning = LearningService(database, kb)
    llm = LLMClient(settings) if settings.llm_ready else None
    assistant = AssistantService(
        database=database,
        settings=settings,
        knowledge=kb,
        llm=llm,
        learning=learning,
        tickets=tickets,
    )
    assets = AssetService(database, settings, panel)
    digest = DigestService(database, settings, kb, notifications)
    workers = Workers(database, settings, kb, status, notifications, assets, digest)
    services = {
        "settings": settings,
        "db": database,
        "database": database,
        "kb": kb,
        "assistant": assistant,
        "tickets": tickets,
        "donations": donations,
        "console": console,
        "status": status,
        "notifier": notifications,
        "learning": learning,
        "audit": audit,
        "panel": panel,
        "assets": assets,
        "digest": digest,
        "workers": workers,
        "bot": bot,
    }
    dispatcher = build_dispatcher(services, settings)
    await kb.ensure_static()
    await assets.sync(from_panel=panel is not None)

    async def on_startup() -> None:
        workers.start()
        await notifications.owner(
            "Eva запущена. Режим: "
            f"{settings.bot_mode}. LLM: {'да' if settings.llm_ready else 'нет'}, "
            f"панель: {'да' if settings.panel_ready else 'нет'}, "
            f"RCON: {'да' if settings.rcon_ready else 'нет'}."
        )

    async def on_shutdown() -> None:
        await workers.shutdown()
        if llm is not None:
            await llm.close()
        if panel is not None:
            await panel.close()
        await database.close()

    dispatcher.startup.register(on_startup)
    dispatcher.shutdown.register(on_shutdown)

    if settings.bot_mode == "polling":
        logger.warning("Polling mode selected: webhook/health endpoint will not be exposed")
        await bot.delete_webhook(drop_pending_updates=settings.drop_pending_updates)
        try:
            await dispatcher.start_polling(bot, drop_pending_updates=settings.drop_pending_updates)
        finally:
            await bot.session.close()
        return

    base = settings.webhook_base
    if not base:
        logger.error(
            "WEBHOOK_BASE_URL is empty; falling back to polling. "
            "Set WEBHOOK_BASE_URL or BOT_MODE=polling."
        )
        await bot.delete_webhook(drop_pending_updates=settings.drop_pending_updates)
        try:
            await dispatcher.start_polling(bot, drop_pending_updates=settings.drop_pending_updates)
        finally:
            await bot.session.close()
        return
    if not settings.webhook_token:
        raise SystemExit("WEBHOOK_SECRET не задан. Webhook без secret_token запрещён.")

    async def health(_: web.Request) -> web.Response:
        db_ok = await database.health()
        return web.json_response(
            {
                "status": "ok" if db_ok else "degraded",
                "db": db_ok,
                "llm": settings.llm_ready,
                "general_mode": settings.allow_general_topics,
                "webhook_secret_source": (
                    "env" if settings.webhook_secret.get_secret_value().strip() else "derived"
                ),
                "panel": bool(panel and panel.ready),
                "rcon": settings.rcon_ready,
                "kb_chunks": await kb.chunk_count(),
            }
        )

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    SimpleRequestHandler(
        dispatcher=dispatcher,
        bot=bot,
        secret_token=settings.webhook_token,
    ).register(app, path=settings.webhook_path)
    setup_application(app, dispatcher, bot=bot)
    await bot.set_webhook(
        settings.webhook_url(),
        secret_token=settings.webhook_token,
        drop_pending_updates=settings.drop_pending_updates,
        allowed_updates=dispatcher.resolve_used_update_types(),
    )
    runner = web.AppRunner(app, handle_signals=False)
    await runner.setup()
    site = web.TCPSite(runner, settings.webhook_host, settings.listen_port)
    await site.start()
    logger.info("Webhook endpoint: %s", settings.webhook_url())
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop_event.set)
    try:
        await stop_event.wait()
    finally:
        await runner.cleanup()
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("Eva stopped")


if __name__ == "__main__":
    main()
