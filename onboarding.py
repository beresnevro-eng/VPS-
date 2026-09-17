"""
Онбординг: FSM-состояния бота + UI-хелперы.
Базовые вопросы — в onboarding_questions.py (общие с Mini App API).
"""

from __future__ import annotations

from typing import Any

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from onboarding_questions import (
    BASE_QUESTIONS,
    FOLLOWUP_COUNT,
    TOTAL_STEPS,
    base_total,
    get_base_question,
    total_steps,
)


class OnboardingStates(StatesGroup):
    """Пошаговое заполнение анкеты."""

    base_question = State()
    generating_followups = State()
    followup_question = State()
    summarizing = State()


class DigestStates(StatesGroup):
    """Диалог после итогов недели."""

    waiting_for_reply = State()


# Фиксированный вопрос после дайджеста (не рандом)
DIGEST_FOLLOWUP_QUESTION = "Что из этого отозвалось сильнее всего?"
CB_DIGEST_SKIP = "digest:skip"


# Mini App URL: Cloudflare Tunnel (без редиректа GitHub Pages — иначе initData пустой).
# HOST_FIX_MINIAPP.sh пишет актуальный URL в .env; читаем при каждом открытии кнопки.
import os

_DEFAULT_MINI_APP = "https://beresnevro-eng.github.io/VPS-/index.html"


def get_mini_app_url() -> str:
    env_url = (os.getenv("MINI_APP_URL") or "").strip()
    if env_url:
        return env_url
    try:
        from dotenv import dotenv_values

        vals = dotenv_values(os.path.join(os.path.dirname(__file__), ".env"))
        file_url = (vals.get("MINI_APP_URL") or "").strip()
        if file_url:
            return file_url
    except Exception:
        pass
    return _DEFAULT_MINI_APP


# совместимость со старым кодом
MINI_APP_URL = get_mini_app_url()


def mini_app_web_info() -> WebAppInfo:
    return WebAppInfo(url=get_mini_app_url())


def mini_app_web_info_for(user_id: int) -> WebAppInfo:
    """Персональный URL с токеном — работает даже без initData от Telegram."""
    from mini_auth import make_mini_app_token

    base = get_mini_app_url()
    token = make_mini_app_token(int(user_id))
    sep = "&" if "?" in base else "?"
    return WebAppInfo(url=f"{base}{sep}t={token}")


# Тексты постоянной клавиатуры (ReplyKeyboard) — всегда под рукой
BTN_SHEPOT = "🌿 Открыть Шёпот"
BTN_QUIZ = "💌 Квиз"
BTN_STATUS = "📊 Статус"
BTN_PROFILE = "👤 Мой профиль"
BTN_DIGEST = "📊 Итоги недели"
BTN_HELP = "❓ Помощь"
BTN_MENU = "🏠 Меню"
BTN_CANCEL = "✖️ Отмена"

# WebApp-кнопка не шлёт текст в чат — в MENU_BUTTON_TEXTS её нет
MENU_BUTTON_TEXTS = {
    BTN_QUIZ,
    BTN_STATUS,
    BTN_PROFILE,
    BTN_DIGEST,
    BTN_HELP,
    BTN_MENU,
    BTN_CANCEL,
}


def main_menu_keyboard(user_id: int | None = None) -> ReplyKeyboardMarkup:
    """Постоянные кнопки: Mini App сверху, дальше движок бота."""
    web = mini_app_web_info_for(user_id) if user_id else mini_app_web_info()
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SHEPOT, web_app=web)],
            [KeyboardButton(text=BTN_QUIZ), KeyboardButton(text=BTN_STATUS)],
            [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_DIGEST)],
            [KeyboardButton(text=BTN_HELP), KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие или ответьте на вопрос…",
    )


def question_keyboard(step: int, question: dict[str, Any]) -> InlineKeyboardMarkup | None:
    """Inline-кнопки для multiple_choice. callback: o:{step}:{idx}"""
    if question.get("type") != "multiple_choice":
        return None
    options = question.get("options") or []
    rows = [
        [InlineKeyboardButton(text=str(opt)[:60], callback_data=f"o:{step}:{i}")]
        for i, opt in enumerate(options)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_base_question(step: int, question: dict[str, Any], *, user_id: int | None = None) -> str:
    total = base_total()
    text = question.get("text") or ""
    tmpl = question.get("text_template")
    if tmpl:
        import config

        name = config.partner_name(user_id) if user_id else "ты"
        text = str(tmpl).format(name=name or "ты")
    hint = (
        "\n\nВыберите вариант 👇"
        if question.get("type") == "multiple_choice"
        else "\n\nНапишите ответ одним сообщением ✍️"
    )
    return f"📋 Анкета {step + 1}/{total}\n\n{text}{hint}"


def format_followup_question(index: int, total: int, text: str) -> str:
    return (
        f"🔎 Уточнение {index + 1}/{total}\n\n{text}\n\n"
        "Напишите ответ своими словами ✍️"
    )
