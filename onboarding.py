"""
Онбординг: 15 базовых вопросов + FSM-состояния.
Ответы пишутся в SQLite через database.save_onboarding_answer (не копятся только в RAM).
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


# Mini App (GitHub Pages).
# Важно: явный index.html — без редиректа /VPS- → /VPS-/, иначе Telegram macOS
# теряет #tgWebAppData=... и initData остаётся пустым.
MINI_APP_URL = "https://beresnevro-eng.github.io/VPS-/index.html"

# Тексты постоянной клавиатуры (ReplyKeyboard) — всегда под рукой
BTN_SHEPOT = "🌿 Открыть Шёпот"
BTN_QUIZ = "💌 Квиз"
BTN_STATUS = "📊 Статус"
BTN_PROFILE = "👤 Мой профиль"
BTN_DIGEST = "📊 Итоги недели"
BTN_HELP = "❓ Помощь"
BTN_MENU = "🏠 Меню"

# WebApp-кнопка не шлёт текст в чат — в MENU_BUTTON_TEXTS её нет
MENU_BUTTON_TEXTS = {BTN_QUIZ, BTN_STATUS, BTN_PROFILE, BTN_DIGEST, BTN_HELP, BTN_MENU}


def mini_app_web_info() -> WebAppInfo:
    return WebAppInfo(url=MINI_APP_URL)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Постоянные кнопки: Mini App сверху, дальше движок бота."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SHEPOT, web_app=mini_app_web_info())],
            [KeyboardButton(text=BTN_QUIZ), KeyboardButton(text=BTN_STATUS)],
            [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_DIGEST)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие или ответьте на вопрос…",
    )


# 15 базовых вопросов (inline или open)
# category → поля профиля: values/love_language/attachment/conflict/intimacy/household
BASE_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "v1",
        "category": "values",
        "type": "multiple_choice",
        "text": "Что для вас важнее всего в отношениях прямо сейчас?",
        "options": [
            "Чувствовать себя в безопасности",
            "Развиваться вместе",
            "Сохранять страсть и игру",
            "Спокойный быт и предсказуемость",
        ],
    },
    {
        "id": "v2",
        "category": "values",
        "type": "multiple_choice",
        "text": "Какую ценность вы готовы отстаивать даже в споре с партнёром?",
        "options": [
            "Честность",
            "Личное пространство",
            "Семья / общие планы",
            "Взаимное уважение границ",
        ],
    },
    {
        "id": "v3",
        "category": "values",
        "type": "open_ended",
        "text": "Напишите одним предложением: что для вас значит «быть хорошим партнёром»?",
        "options": [],
    },
    {
        "id": "ll1",
        "category": "love_language",
        "type": "multiple_choice",
        "text": "Как вы чаще всего чувствуете любовь партнёра?",
        "options": [
            "Слова поддержки и комплименты",
            "Время вместе без отвлечений",
            "Помощь и дела по дому",
            "Прикосновения и нежность",
        ],
    },
    {
        "id": "ll2",
        "category": "love_language",
        "type": "multiple_choice",
        "text": "Как вам естественнее проявлять заботу?",
        "options": [
            "Говорить тёплые слова",
            "Делать практичные вещи",
            "Дарить внимание и время",
            "Обнимать / быть рядом физически",
        ],
    },
    {
        "id": "ll3",
        "category": "love_language",
        "type": "multiple_choice",
        "text": "Что сильнее всего ранит, если партнёр этого не даёт?",
        "options": [
            "Молчание и холодные слова",
            "Игнор совместного времени",
            "Отказ помочь в быту",
            "Нехватка телесной близости",
        ],
    },
    {
        "id": "a1",
        "category": "attachment",
        "type": "multiple_choice",
        "text": "Когда партнёр отвечает не сразу, что вы чувствуете чаще?",
        "options": [
            "Спокойно жду",
            "Немного тревожусь",
            "Злюсь / закрываюсь",
            "Начинаю проверять / писать ещё",
        ],
    },
    {
        "id": "a2",
        "category": "attachment",
        "type": "multiple_choice",
        "text": "В близости вам обычно комфортнее…",
        "options": [
            "Быть очень близко и делиться всем",
            "Баланс близости и своего пространства",
            "Держать дистанцию, чтобы не ранить",
            "То сближаться, то отдаляться",
        ],
    },
    {
        "id": "a3",
        "category": "attachment",
        "type": "open_ended",
        "text": "Вспомните момент, когда вам было особенно важно, чтобы партнёр «был рядом». Что вам тогда было нужно?",
        "options": [],
    },
    {
        "id": "c1",
        "category": "conflict",
        "type": "multiple_choice",
        "text": "Как вы обычно ведёте себя в конфликте?",
        "options": [
            "Говорю сразу и прямо",
            "Нужна пауза, потом возвращаюсь",
            "Избегаю эскалации, сглаживаю",
            "Могу вспылить, потом жалею",
        ],
    },
    {
        "id": "c2",
        "category": "conflict",
        "type": "multiple_choice",
        "text": "Что помогает вам быстрее помириться?",
        "options": [
            "Извинение и признание чувств",
            "Практическое решение проблемы",
            "Объятие / физический контакт",
            "Время и тишина",
        ],
    },
    {
        "id": "h1",
        "category": "household",
        "type": "multiple_choice",
        "text": "Как вы относитесь к домашним обязанностям?",
        "options": [
            "Чёткое разделение зон",
            "Гибко, кто свободен — тот делает",
            "Часто устаю от неравномерности",
            "Предпочитаю делегировать / упрощать",
        ],
    },
    {
        "id": "h2",
        "category": "household",
        "type": "multiple_choice",
        "text": "Что сильнее раздражает в быту?",
        "options": [
            "Беспорядок",
            "Невыполненные договорённости",
            "Критика моего способа делать",
            "Ощущение, что всё на мне",
        ],
    },
    {
        "id": "i1",
        "category": "intimacy",
        "type": "multiple_choice",
        "text": "Как вы воспринимаете физическую близость в паре?",
        "options": [
            "Важный язык любви",
            "Приятно, но не главное",
            "Нужен правильный эмоциональный климат",
            "Сейчас это сложная / чувствительная тема",
        ],
    },
    {
        "id": "i2",
        "category": "intimacy",
        "type": "open_ended",
        "text": "Что помогает вам чувствовать себя желанным(ой) и в безопасности в близости? Напишите коротко.",
        "options": [],
    },
]

assert len(BASE_QUESTIONS) == 15, "Нужно ровно 15 базовых вопросов"


def base_total() -> int:
    return len(BASE_QUESTIONS)


def get_base_question(step: int) -> dict[str, Any] | None:
    if 0 <= step < len(BASE_QUESTIONS):
        return BASE_QUESTIONS[step]
    return None


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


def format_base_question(step: int, question: dict[str, Any]) -> str:
    total = base_total()
    hint = (
        "\n\nВыберите вариант 👇"
        if question.get("type") == "multiple_choice"
        else "\n\nНапишите ответ одним сообщением ✍️"
    )
    return f"📋 Анкета {step + 1}/{total}\n\n{question['text']}{hint}"


def format_followup_question(index: int, total: int, text: str) -> str:
    return (
        f"🔎 Уточнение {index + 1}/{total}\n\n{text}\n\n"
        "Напишите ответ своими словами ✍️"
    )
