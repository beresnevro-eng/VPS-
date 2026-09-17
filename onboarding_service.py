"""
Сервис онбординга для Mini App API.
Шаги 0–14: базовые вопросы; 15–17: AI-уточнения; после 18-го — портрет.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import ai_service
from database import (
    _load_answers_dict,
    complete_profile,
    get_or_create_profile,
    get_session,
    mark_onboarding_done_pending_summary,
    profile_answers_for_ai,
    profile_answers_split,
    reset_onboarding,
    save_followup_answer,
    save_followup_questions,
    save_onboarding_answer,
)
from onboarding_questions import (
    FOLLOWUP_COUNT,
    TOTAL_STEPS,
    base_total,
    get_base_question,
    serialize_base_question,
    total_steps,
)

logger = logging.getLogger(__name__)

SKIP_MARKER = "(пропущено)"


def _load_followups(profile) -> list[str]:
    try:
        raw = json.loads(profile.followup_json or "[]")
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()][:FOLLOWUP_COUNT]
    except Exception:
        pass
    return []


def _answers_public(profile) -> dict[str, Any]:
    raw = _load_answers_dict(profile)
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            out[str(k)] = {
                "q": v.get("q"),
                "a": v.get("a"),
                "category": v.get("category"),
                "step": v.get("step"),
                "skipped": str(v.get("a") or "").strip() == SKIP_MARKER,
            }
        else:
            out[str(k)] = {"a": v}
    return out


def _current_question(profile) -> dict[str, Any] | None:
    if profile.is_completed:
        return None
    step = max(0, int(profile.onboarding_step or 0))
    n_base = base_total()

    if step < n_base:
        return serialize_base_question(step, user_id=int(profile.user_id))

    fu = _load_followups(profile)
    fu_idx = step - n_base
    if not fu or fu_idx < 0 or fu_idx >= len(fu):
        return None
    return {
        "index": step,
        "id": f"followup_{fu_idx}",
        "text": fu[fu_idx],
        "type": "open_ended",
        "options": [],
        "category": "followup",
    }


def build_onboarding_state(profile) -> dict[str, Any]:
    completed = bool(profile.is_completed)
    step = TOTAL_STEPS if completed else max(0, int(profile.onboarding_step or 0))
    return {
        "step": step,
        "total_steps": total_steps(),
        "is_completed": completed,
        "current_question": _current_question(profile),
        "answers": _answers_public(profile),
        "ai_summary": profile.ai_summary if completed else None,
        "ai_summary_public": profile.ai_summary_public if completed else None,
        "ai_summary_private": profile.ai_summary_private if completed else None,
        "needs_followups": (
            not completed
            and step >= base_total()
            and not _load_followups(profile)
        ),
    }


async def _load_profile(telegram_id: int):
    async with get_session() as session:
        return await get_or_create_profile(session, telegram_id)


async def _ensure_followups(telegram_id: int) -> list[str]:
    async with get_session() as session:
        profile = await get_or_create_profile(session, telegram_id)
        existing = _load_followups(profile)
        if existing:
            if int(profile.onboarding_step or 0) < base_total():
                profile.onboarding_step = base_total()
                await session.commit()
            return existing
        answers = profile_answers_for_ai(profile)

    questions = await ai_service.generate_followup_questions(answers)
    async with get_session() as session:
        await save_followup_questions(session, telegram_id, questions)
        profile = await get_or_create_profile(session, telegram_id)
        profile.onboarding_step = max(int(profile.onboarding_step or 0), base_total())
        await session.commit()
    return questions[:FOLLOWUP_COUNT]


async def _finalize(telegram_id: int) -> dict[str, Any]:
    async with get_session() as session:
        profile = await get_or_create_profile(session, telegram_id)
        base_ans, fu_ans = profile_answers_split(profile)

    summary, public, private, ok = await ai_service.generate_profile_summary(
        base_ans, fu_ans, user_id=telegram_id
    )
    async with get_session() as session:
        if ok:
            await complete_profile(
                session,
                telegram_id,
                summary,
                ai_summary_public=public,
                ai_summary_private=private,
            )
        else:
            await mark_onboarding_done_pending_summary(session, telegram_id)
        profile = await get_or_create_profile(session, telegram_id)
        return build_onboarding_state(profile)


async def get_onboarding_state(telegram_id: int) -> dict[str, Any]:
    profile = await _load_profile(telegram_id)
    if profile.is_completed:
        return build_onboarding_state(profile)

    step = max(0, int(profile.onboarding_step or 0))
    if step >= base_total() and not _load_followups(profile):
        await _ensure_followups(telegram_id)
        profile = await _load_profile(telegram_id)

    if (
        step >= base_total() + FOLLOWUP_COUNT
        and not profile.is_completed
    ):
        return await _finalize(telegram_id)

    return build_onboarding_state(profile)


async def submit_onboarding_answer(
    telegram_id: int,
    *,
    step: int,
    answer: str | None,
    skipped: bool = False,
) -> dict[str, Any]:
    """
    Сохраняет ответ на step и возвращает новое состояние.
    Идемпотентность: повторная отправка уже пройденного step → текущее состояние.
    """
    profile = await _load_profile(telegram_id)
    if profile.is_completed:
        return build_onboarding_state(profile)

    cur = max(0, int(profile.onboarding_step or 0))
    if step < cur:
        return build_onboarding_state(profile)
    if step > cur:
        raise ValueError(f"Ожидается шаг {cur}, получен {step}")

    n_base = base_total()
    text = SKIP_MARKER if skipped else str(answer or "").strip()
    if not skipped and not text:
        raise ValueError("Пустой ответ")

    if step < n_base:
        q = get_base_question(step)
        if not q:
            raise ValueError("Вопрос не найден")
        next_step = step + 1
        async with get_session() as session:
            await save_onboarding_answer(
                session,
                telegram_id,
                step=step,
                question_id=str(q.get("id") or f"q{step}"),
                question_text=str(q["text"]),
                answer_text=text,
                category=str(q.get("category") or ""),
                next_step=next_step,
            )
        if next_step >= n_base:
            await _ensure_followups(telegram_id)
        return await get_onboarding_state(telegram_id)

    # follow-up steps 15..17
    fu_idx = step - n_base
    fu = _load_followups(profile)
    if not fu:
        await _ensure_followups(telegram_id)
        profile = await _load_profile(telegram_id)
        fu = _load_followups(profile)
    if fu_idx < 0 or fu_idx >= len(fu):
        raise ValueError("Нет такого уточнения")

    next_step = step + 1
    async with get_session() as session:
        await save_followup_answer(
            session,
            telegram_id,
            index=fu_idx,
            question_text=fu[fu_idx],
            answer_text=text,
            next_step=next_step,
        )

    if next_step >= n_base + FOLLOWUP_COUNT:
        return await _finalize(telegram_id)
    return await get_onboarding_state(telegram_id)


async def reset_onboarding_state(telegram_id: int) -> dict[str, Any]:
    async with get_session() as session:
        profile = await reset_onboarding(session, telegram_id)
        return build_onboarding_state(profile)
