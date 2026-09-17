"""
Базовые вопросы онбординга (общие для бота и Mini App).
Формулировки единые — опыт консистентен.
Первый вопрос — род обращения (gender).
"""

from __future__ import annotations

from typing import Any

from gender_utils import GENDER_OPT_FEMALE, GENDER_OPT_MALE, GENDER_OPT_NEUTRAL

# category → поля UserProfile (см. database.FIELD_BY_CATEGORY)
BASE_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "gender",
        "category": "gender",
        "type": "multiple_choice",
        "text": "Укажи, как к тебе обращаться:",
        "text_template": "Ты — {name}. Укажи, как к тебе обращаться:",
        "options": [
            GENDER_OPT_FEMALE,
            GENDER_OPT_MALE,
            GENDER_OPT_NEUTRAL,
        ],
    },
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

assert len(BASE_QUESTIONS) == 16, "Нужно 16 базовых вопросов (gender + 15)"

FOLLOWUP_COUNT = 3
TOTAL_STEPS = len(BASE_QUESTIONS) + FOLLOWUP_COUNT  # 19


def base_total() -> int:
    return len(BASE_QUESTIONS)


def total_steps() -> int:
    return TOTAL_STEPS


def get_base_question(step: int) -> dict[str, Any] | None:
    if 0 <= step < len(BASE_QUESTIONS):
        return BASE_QUESTIONS[step]
    return None


def serialize_base_question(
    step: int, *, user_id: int | None = None, name: str | None = None
) -> dict[str, Any] | None:
    q = get_base_question(step)
    if not q:
        return None
    text = q["text"]
    tmpl = q.get("text_template")
    if tmpl:
        display_name = name
        if not display_name and user_id:
            import config

            display_name = config.partner_name(int(user_id))
        text = str(tmpl).format(name=display_name or "ты")
    return {
        "index": step,
        "id": q.get("id"),
        "text": text,
        "type": q.get("type") or "open_ended",
        "options": list(q.get("options") or []),
        "category": q.get("category") or "",
    }
