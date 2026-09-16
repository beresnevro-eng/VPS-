"""
Middleware: обязательный онбординг (gating).
Сверяйся с MANIFESTO.md — без портрета квизы и аналитика не открываем.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

import config
import onboarding as ob
from database import get_session, profile_is_completed

logger = logging.getLogger(__name__)

_ALLOWED_COMMANDS = {"start", "help", "cancel", "onboarding", "reset_topics"}
_ALLOWED_MENU_TEXTS = {ob.BTN_HELP}

_GATE_TEXT = (
    "Прежде чем мы начнём, мне нужно узнать тебя поближе. "
    "Это займёт 5 минут, но сделает наши инсайты в разы глубже. "
    "Давай заполним анкету!"
)


def _extract_command(text: str | None) -> Optional[str]:
    if not text or not text.startswith("/"):
        return None
    return text[1:].split()[0].split("@")[0].lower() or None


class OnboardingGateMiddleware(BaseMiddleware):
    """Блокирует функции, пока UserProfile.is_completed == False."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        if user.id not in config.allowed_user_ids():
            return await handler(event, data)

        state: FSMContext | None = data.get("state")
        current_state = await state.get_state() if state else None

        if current_state and str(current_state).startswith("OnboardingStates"):
            return await handler(event, data)

        # Диалог после дайджеста не блокируем гейтом
        if current_state and str(current_state).startswith("DigestStates"):
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and (event.data or "").startswith("o:"):
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and (event.data or "") == ob.CB_DIGEST_SKIP:
            return await handler(event, data)

        if isinstance(event, Message) and event.text:
            cmd = _extract_command(event.text)
            if cmd in _ALLOWED_COMMANDS:
                return await handler(event, data)
            if event.text in _ALLOWED_MENU_TEXTS:
                return await handler(event, data)

        async with get_session() as session:
            done = await profile_is_completed(session, user.id)
        if done:
            return await handler(event, data)

        logger.info("Onboarding gate: block user=%s", user.id)
        from handlers import start_onboarding

        if not state:
            return None

        if isinstance(event, CallbackQuery):
            await event.answer("Сначала анкета", show_alert=False)
            if event.message:
                await event.message.answer(_GATE_TEXT, reply_markup=ob.main_menu_keyboard())
                await start_onboarding(
                    event.message, state, resume=True, telegram_id=user.id
                )
            return None

        if isinstance(event, Message):
            await event.answer(_GATE_TEXT, reply_markup=ob.main_menu_keyboard())
            await start_onboarding(event, state, resume=True, telegram_id=user.id)
            return None

        return None
