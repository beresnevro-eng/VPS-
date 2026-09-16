"""
Аналитический слой Люма / «Шёпот» (см. MANIFESTO.md).
Ежедневный инсайт по квизу и недельные итоги через Groq.
Лёгкий код под 1 ГБ RAM: данные читаем из SQLite, в память не копим.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import ai_service
import config
from database import (
    get_session,
    load_quiz_for_analysis,
    save_weekly_digest,
    weekly_quiz_facts,
    get_or_create_profile,
)

logger = logging.getLogger(__name__)


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Достаёт JSON-объект из ответа модели."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("В ответе AI нет JSON-объекта")
    obj = json.loads(text[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("Ожидался JSON-объект")
    return obj


def _format_answers_block(payload: dict) -> str:
    lines = [f"Тема квиза: {payload.get('topic', '—')}", ""]
    for i, q in enumerate(payload.get("questions", []), start=1):
        construct = q.get("psychological_construct") or ""
        qtype = q.get("type") or ""
        extra = f" [{construct}]" if construct else ""
        lines.append(f"Вопрос {i}{extra} ({qtype}): {q['text']}")
        for ans in q.get("answers", []):
            if ans.get("skipped"):
                lines.append(f"  — {ans['name']}: пропущено")
            elif ans.get("selected_option"):
                lines.append(f"  — {ans['name']}: выбрал(а) «{ans['selected_option']}»")
            else:
                lines.append(f"  — {ans['name']}: {ans['text']}")
        lines.append("")
    return "\n".join(lines)


async def analyze_daily_quiz(quiz_id: int) -> tuple[str, bool]:
    """
    Сравнивает ответы обоих партнёров на квиз.
    Returns: (текст для Telegram, ok).
    """
    async with get_session() as session:
        payload = await load_quiz_for_analysis(session, quiz_id)

    answers_block = _format_answers_block(payload)
    logger.info(
        "analyze_daily_quiz DEBUG quiz_id=%s answers_len=%s",
        quiz_id,
        len(answers_block),
    )
    print(
        f"[Lumen DEBUG] daily quiz #{quiz_id} answers:\n{answers_block[:2500]}",
        flush=True,
    )

    system = (
        f"{ai_service.voice_system_preamble()} "
        "Ты — Люм. Сравни ответы пары на сегодняшний квиз. "
        "Найди 1 совпадение и 1 различие. Дай мягкую рекомендацию на сегодня. "
        "Обращайся к паре на «вы». Начни со слов «💡 Наши инсайты». "
        "Не используй слово «проблема». Вместо этого используй "
        "«зона роста» или «точка внимания». "
        "Подпишись как Люм. В конце — один открытый вопрос."
    )
    user = f"Ответы пары на сегодняшний квиз:\n\n{answers_block}"

    try:
        text = await ai_service._chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.6,
            max_tokens=1100,
        )
        if not text or len(text.strip()) < 30:
            raise RuntimeError("пустой инсайт от Groq")
        if not text.strip().startswith("💡"):
            text = f"💡 Наши инсайты\n\n{text.strip()}"
        return ai_service.as_lumen(ai_service.with_open_question(text)), True
    except Exception as exc:
        logger.exception("analyze_daily_quiz failed: %s", exc)
        print(f"[Lumen DEBUG] daily analysis ERROR: {exc}", flush=True)
        return (
            ai_service.as_lumen(
                "🌿 Люм немного задумался... Давай попробуем собрать инсайт "
                "по квизу чуть позже."
            ),
            False,
        )


async def generate_weekly_digest(user_id_1: int, user_id_2: int = 0) -> tuple[str, int]:
    """
    Собирает ответы за 7 дней + портреты UserProfile.
    Возвращает (текст для Telegram, id записи WeeklyDigest).
    Открытый вопрос задаёт handlers + DigestStates (не здесь).
    """
    async with get_session() as session:
        facts = await weekly_quiz_facts(session, days=7)
        p1 = await get_or_create_profile(session, user_id_1)
        p2 = await get_or_create_profile(session, user_id_2) if user_id_2 else None

        portraits = []
        for label, p, uid in (
            ("Партнёр 1", p1, user_id_1),
            ("Партнёр 2", p2, user_id_2),
        ):
            if not p or not uid:
                continue
            portraits.append(
                f"{label} (tg={uid}):\n"
                f"ценности: {(p.core_values or '—')[:300]}\n"
                f"язык заботы: {(p.love_language or '—')[:300]}\n"
                f"близость: {(p.intimacy_views or '—')[:300]}\n"
                f"портрет: {(p.ai_summary or 'ещё нет')[:500]}"
            )
        portraits_block = "\n\n".join(portraits) or "Портреты пока пустые."

    system = (
        f"{ai_service.voice_system_preamble()} "
        "Ты — аналитик отношений, но говоришь как мудрый друг. "
        "На основе портретов пары и их ответов за неделю выяви 1 главный паттерн. "
        "Предложи 3 конкретных действия на следующую неделю. "
        "Верни строго JSON: "
        '{"pattern": "...", "actions": ["...", "...", "..."]}. '
        "Без пояснений вне JSON. Без слова «проблема»."
    )
    user = (
        f"Портреты:\n{portraits_block[:2200]}\n\n"
        f"Факты недели:\n{facts[:1500]}"
    )

    pattern = "Вы уделяли внимание диалогу — это уже сильная опора вашей связи."
    actions = [
        "10 минут вместе без телефонов",
        "Один жест заботы в языке любви партнёра",
        "Мягкий check-in вечером: «Как ты сегодня?»",
    ]
    raw_json = "{}"

    try:
        raw = await ai_service._chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.55,
            max_tokens=700,
        )
        obj = _extract_json_object(raw)
        raw_json = json.dumps(obj, ensure_ascii=False)
        pattern = str(obj.get("pattern") or pattern).strip()
        acts = obj.get("actions") or []
        if isinstance(acts, list) and acts:
            actions = [str(a).strip() for a in acts if str(a).strip()][:3]
            while len(actions) < 3:
                actions.append("Мягкий вечерний check-in друг с другом")
    except Exception as exc:
        logger.exception("generate_weekly_digest AI failed: %s", exc)
        raw_json = json.dumps(
            {"pattern": pattern, "actions": actions}, ensure_ascii=False
        )

    actions_lines = "\n".join(f"{i}. {a}" for i, a in enumerate(actions, 1))
    formatted = ai_service.as_lumen(
        f"📊 Итоги недели\n\n"
        f"Я, {config.ASSISTANT_NAME}, посмотрел на вашу неделю в «{config.PROJECT_NAME}».\n\n"
        f"🔎 Главный паттерн:\n{pattern}\n\n"
        f"✨ Три шага на следующую неделю:\n{actions_lines}"
    )

    async with get_session() as session:
        row = await save_weekly_digest(
            session,
            user_id_1=user_id_1,
            user_id_2=user_id_2 or 0,
            pattern=pattern,
            actions=actions,
            raw_json=raw_json,
            formatted_text=formatted,
        )
        digest_id = row.id

    return formatted, digest_id
