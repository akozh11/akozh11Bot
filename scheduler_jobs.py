"""
Задачи APScheduler:
- периодический опрос отслеживаемых репозиториев (модуль A)
- еженедельный отчёт по воскресеньям в 09:00 (модуль E)
"""
from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import analytics_service
import config
import github_service
import pipeline
import telegram_utils

log = logging.getLogger(__name__)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)

    scheduler.add_job(
        poll_repos_job,
        trigger=IntervalTrigger(minutes=config.POLL_INTERVAL_MINUTES),
        args=[bot],
        id="poll_tracked_repos",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        weekly_report_job,
        trigger=CronTrigger(day_of_week="sun", hour=9, minute=0),
        args=[bot],
        id="weekly_report",
        max_instances=1,
    )

    return scheduler


async def poll_repos_job(bot: Bot) -> None:
    log.info("Запуск планового опроса репозиториев")

    async def _on_new_content(repo_row, dedup_suffix: str) -> None:
        await pipeline.process_new_repo_event(
            bot,
            full_name=repo_row["full_name"],
            url=repo_row["url"],
            dedup_suffix=dedup_suffix,
            source=repo_row["source"],
        )

    await github_service.poll_tracked_repos(_on_new_content)


async def weekly_report_job(bot: Bot) -> None:
    log.info("Формирую еженедельный отчёт")
    text = await analytics_service.build_weekly_report_text()
    for admin_id in config.ADMIN_IDS:
        await telegram_utils.safe_send_message(bot, admin_id, text)
