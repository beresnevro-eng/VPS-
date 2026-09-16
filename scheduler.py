"""
Ежедневный квиз + воскресный дайджест (APScheduler).
"""

from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from handlers import send_weekly_digest, start_daily_quiz

logger = logging.getLogger(__name__)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)

    async def daily_job() -> None:
        logger.info("Плановый квиз %02d:%02d", config.QUIZ_HOUR, config.QUIZ_MINUTE)
        try:
            await start_daily_quiz(bot, automatic=True)
        except Exception:
            logger.exception("Ошибка планового квиза")

    async def digest_job() -> None:
        logger.info("Воскресный дайджест")
        try:
            await send_weekly_digest(bot)
        except Exception:
            logger.exception("Ошибка дайджеста")

    scheduler.add_job(
        daily_job,
        CronTrigger(hour=config.QUIZ_HOUR, minute=config.QUIZ_MINUTE),
        id="daily_couple_quiz",
        replace_existing=True,
    )
    # Воскресенье = sun
    scheduler.add_job(
        digest_job,
        CronTrigger(
            day_of_week="sun",
            hour=config.DIGEST_HOUR,
            minute=config.DIGEST_MINUTE,
        ),
        id="weekly_couple_digest",
        replace_existing=True,
    )
    return scheduler
