"""
Склонение / род обращения к партнёрам (male | female | null).
"""

from __future__ import annotations

from typing import Any, Optional, Union

import config

GenderLike = Union[str, None, Any]  # profile / user_id / "male"|"female"

# Явные подсказки по именам пары (если gender ещё не выбран в анкете)
_NAME_GENDER: dict[str, str] = {
    "рома": "male",
    "роман": "male",
    "юля": "female",
    "юлия": "female",
}

GENDER_OPT_FEMALE = "♀ Женский род (ответила, закончила)"
GENDER_OPT_MALE = "♂ Мужской род (ответил, закончил)"
GENDER_OPT_NEUTRAL = "💬 Не важно — используй «ты» без рода"

GENDER_ANSWER_MAP: dict[str, Optional[str]] = {
    GENDER_OPT_FEMALE: "female",
    GENDER_OPT_MALE: "male",
    GENDER_OPT_NEUTRAL: None,
}


def normalize_gender(raw: Any) -> Optional[str]:
    val = (str(raw).strip().lower() if raw is not None else "")
    if val in ("male", "m", "м", "муж", "мужской"):
        return "male"
    if val in ("female", "f", "ж", "жен", "женский"):
        return "female"
    return None


def gender_from_answer_text(answer: str) -> Optional[str]:
    """Маппинг варианта онбординга → male|female|None."""
    text = (answer or "").strip()
    if text in GENDER_ANSWER_MAP:
        return GENDER_ANSWER_MAP[text]
    low = text.lower()
    if "женск" in low or "ответила" in low:
        return "female"
    if "мужск" in low or "ответил," in low or "ответил)" in low:
        return "male"
    if "не важно" in low or "без рода" in low:
        return None
    return normalize_gender(text)


def gender_from_name(name: str) -> Optional[str]:
    key = (name or "").strip().lower()
    if key in _NAME_GENDER:
        return _NAME_GENDER[key]
    # эвристика: имена на -а/-я чаще женские (кроме редких исключений)
    if len(key) >= 2 and key[-1] in "ая" and key not in ("никита", "илья", "кузьма"):
        return "female"
    return None


def config_gender_for(user_id: int) -> Optional[str]:
    """Опционально PARTNER_A_GENDER / PARTNER_B_GENDER в .env."""
    import os

    if user_id and user_id == config.PARTNER_A_ID:
        return normalize_gender(os.getenv("PARTNER_A_GENDER", ""))
    if user_id and user_id == config.PARTNER_B_ID:
        return normalize_gender(os.getenv("PARTNER_B_GENDER", ""))
    return None


def resolve_gender(profile_or_user_id: GenderLike, *, name: str | None = None) -> Optional[str]:
    """
    Возвращает male|female|None.
    Приоритет: profile.gender → config env → имя → None.
    """
    if profile_or_user_id is None:
        return gender_from_name(name or "") or None

    # Уже строка gender
    if isinstance(profile_or_user_id, str) and profile_or_user_id in ("male", "female"):
        return profile_or_user_id

    # UserProfile-like
    if hasattr(profile_or_user_id, "gender") or hasattr(profile_or_user_id, "user_id"):
        g = normalize_gender(getattr(profile_or_user_id, "gender", None))
        if g:
            return g
        uid = int(getattr(profile_or_user_id, "user_id", 0) or 0)
        cg = config_gender_for(uid)
        if cg:
            return cg
        nm = name or config.partner_name(uid)
        return gender_from_name(nm)

    # telegram id
    try:
        uid = int(profile_or_user_id)
    except (TypeError, ValueError):
        return gender_from_name(name or "")

    cg = config_gender_for(uid)
    if cg:
        return cg
    return gender_from_name(name or config.partner_name(uid))


def gendered(
    profile_or_user_id: GenderLike,
    male_form: str,
    female_form: str,
    neutral_form: str | None = None,
) -> str:
    """
    Выбирает форму по роду.
    Если gender не указан — neutral_form, иначе male_form (безопасный дефолт только
    когда нейтраль не задана; для Юли сработает эвристика имени / env).
    """
    g = resolve_gender(profile_or_user_id)
    if g == "female":
        return female_form
    if g == "male":
        return male_form
    if neutral_form is not None:
        return neutral_form
    return male_form


def gender_label_ru(profile_or_user_id: GenderLike) -> str:
    g = resolve_gender(profile_or_user_id)
    if g == "female":
        return "женском"
    if g == "male":
        return "мужском"
    return "нейтральном"


def ai_gender_instruction(profile_or_user_id: GenderLike) -> str:
    g = resolve_gender(profile_or_user_id)
    if g == "female":
        rod = "женском"
    elif g == "male":
        rod = "мужском"
    else:
        return (
            "Обращайся к пользователю нейтрально, без женского/мужского рода "
            "в прошедшем времени (или используй «ты» без рода), если это применимо."
        )
    return (
        f"Обращайся к пользователю в {rod} роде, если это применимо. "
        "Если род неуместен в формулировке — используй нейтральные формы."
    )
