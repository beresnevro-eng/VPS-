"""Runtime-объекты, общие для бота и Mini App API (один процесс)."""

from __future__ import annotations

from typing import Optional

from aiogram import Bot

_bot: Optional[Bot] = None


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def get_bot() -> Bot:
    if _bot is None:
        raise RuntimeError("Bot ещё не инициализирован")
    return _bot


def try_get_bot() -> Optional[Bot]:
    return _bot
