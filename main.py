"""
Точка входа: инициализация БД, бота, роутеров, планировщика и
вспомогательного aiohttp-сервера (GitHub webhook + редиректы кликов).
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

import config
import database
import handlers
import scheduler_jobs
import webhook_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


async def _seed_tracked_repos() -> None:
    """Регистрирует в БД репозитории из TRACKED_REPOS (сторонние/трендовые
    проекты, за которыми следим через периодический опрос REST API)."""
    for slug in config.TRACKED_REPOS:
        url = f"https://github.com/{slug}"
        await database.upsert_tracked_repo(slug, url, source="trending")


async def main() -> None:
    config.validate()
    await database.init_db()
    await _seed_tracked_repos()

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(handlers.router)

    scheduler = scheduler_jobs.setup_scheduler(bot)
    scheduler.start()

    app = webhook_server.create_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.WEBHOOK_HOST, config.WEBHOOK_PORT)
    await site.start()
    log.info(
        "Веб-сервер (GitHub webhook + редиректы кликов) запущен на %s:%s",
        config.WEBHOOK_HOST,
        config.WEBHOOK_PORT,
    )

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        log.info("Запускаю long polling бота…")
        # resolve_used_update_types() гарантирует, что Telegram пришлёт в том
        # числе message_reaction_count — он нужен для подсчёта реакций.
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
