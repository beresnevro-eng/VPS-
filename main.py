"""
Точка входа: polling Telegram-бота + лёгкий FastAPI (Mini App) + scheduler.
Один процесс, один event loop — без Gunicorn/воркеров (экономия RAM).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

for _pk in (
    "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
    "ALL_PROXY", "all_proxy", "SOCKS_PROXY", "SOCKS5_PROXY",
    "socks_proxy", "socks5_proxy",
):
    os.environ.pop(_pk, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
import uvicorn

load_dotenv()

import config  # noqa: E402
from api import app as api_app  # noqa: E402
from database import ensure_partners, init_db  # noqa: E402
from handlers import setup_dispatcher  # noqa: E402
from scheduler import setup_scheduler  # noqa: E402


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


async def run_api() -> None:
    """Uvicorn в том же asyncio-loop (1 воркер, минимальная RAM)."""
    uv_config = uvicorn.Config(
        api_app,
        host=config.API_HOST,
        port=config.API_PORT,
        log_level="info",
        access_log=False,
        workers=1,
        loop="asyncio",
    )
    # Не даём uvicorn.sys.exit уронить весь процесс (бот + scheduler)
    uv_config.lifespan = "on"
    server = uvicorn.Server(uv_config)
    server.install_signal_handlers = False
    logging.info("Mini App API: http://%s:%s", config.API_HOST, config.API_PORT)
    try:
        await server.serve()
    except SystemExit as exc:
        logging.error(
            "Mini App API не стартовал (код %s). Бот продолжит работать без API.",
            getattr(exc, "code", exc),
        )
    except OSError as exc:
        logging.error("Mini App API bind error: %s", exc)


async def run_bot(bot: Bot, dp: Dispatcher, scheduler) -> None:
    try:
        # Menu Button слева от поля ввода + дубль на клавиатуре
        try:
            from aiogram.types import MenuButtonWebApp
            import onboarding as ob

            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Шёпот",
                    web_app=ob.mini_app_web_info(),
                )
            )
            logging.info("Menu Button → %s", ob.get_mini_app_url())
        except Exception:
            logging.exception("Не удалось выставить Menu Button")

        # handle_signals=False — иначе конфликт с uvicorn в одном процессе
        await dp.start_polling(
            bot,
            drop_pending_updates=True,
            handle_signals=False,
        )
    except Exception:
        logging.exception("Telegram polling остановился")
        raise
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


async def main() -> None:
    setup_logging()
    problems = config.validate_runtime_config()
    if problems:
        for p in problems:
            logging.error("Конфиг: %s", p)
        raise SystemExit(
            "Задайте секреты в /root/couple-quiz-bot/.env и перезапустите.\n"
            "Нужны: BOT_TOKEN, PARTNER_A_ID и DEEPSEEK_API_KEY (или GROQ_API_KEY)"
        )

    await init_db()
    await ensure_partners()

    bot = Bot(token=config.BOT_TOKEN)
    import runtime as runtime_mod  # noqa: WPS433

    runtime_mod.set_bot(bot)
    dp = Dispatcher(storage=MemoryStorage())
    setup_dispatcher(dp)

    scheduler = setup_scheduler(bot)
    scheduler.start()
    logging.info(
        "Бот запущен. Квиз %02d:%02d, дайджест вс %02d:%02d (%s)",
        config.QUIZ_HOUR,
        config.QUIZ_MINUTE,
        config.DIGEST_HOUR,
        config.DIGEST_MINUTE,
        config.TIMEZONE,
    )

    # Параллельно: polling + FastAPI. Ошибка API не должна гасить бота.
    results = await asyncio.gather(
        run_api(),
        run_bot(bot, dp, scheduler),
        return_exceptions=True,
    )
    for label, result in zip(("api", "bot"), results):
        if isinstance(result, BaseException) and not isinstance(result, SystemExit):
            logging.error("%s завершился с ошибкой: %s", label, result, exc_info=result)


if __name__ == "__main__":
    asyncio.run(main())
