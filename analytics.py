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


def _local_match_stats(payload: dict) -> tuple[int, int]:
    """Грубая эвристика совпадений + число вопросов (fallback, если AI не дал match_count)."""
    questions = payload.get("questions") or []
    qcount = len(questions)
    matches = 0
    for q in questions:
        answers = [a for a in (q.get("answers") or []) if not a.get("skipped")]
        if len(answers) < 2:
            continue
        opts = [(a.get("selected_option") or "").strip() for a in answers]
        texts = [(a.get("text") or "").strip().lower() for a in answers]
        if opts[0] and opts[1] and opts[0] == opts[1]:
            matches += 1
        elif texts[0] and texts[1] and texts[0] == texts[1]:
            matches += 1
    return matches, qcount


def _clamp_int(value: Any, lo: int, hi: int, default: int | None = None) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


async def analyze_daily_quiz(quiz_id: int) -> tuple[str, dict[str, Any], bool]:
    """
    Сравнивает ответы обоих партнёров на квиз.

    Returns:
        (текст для Telegram, metrics, ok)
        metrics: temperature_score, match_count, question_count (при ok).
    """
    async with get_session() as session:
        payload = await load_quiz_for_analysis(session, quiz_id)

    answers_block = _format_answers_block(payload)
    local_matches, question_count = _local_match_stats(payload)
    logger.info(
        "analyze_daily_quiz DEBUG quiz_id=%s answers_len=%s q=%s",
        quiz_id,
        len(answers_block),
        question_count,
    )
    print(
        f"[Lumen DEBUG] daily quiz #{quiz_id} answers:\n{answers_block[:2500]}",
        flush=True,
    )

    system = (
        f"{ai_service.voice_system_preamble()} "
        "Ты — Люм. Сравни ответы пары на сегодняшний квиз. "
        "Найди 1 совпадение и 1 различие. Дай мягкую рекомендацию на сегодня. "
        "Обращайся к паре на «вы». В поле analysis начни со слов «💡 Наши инсайты». "
        "Не используй слово «проблема». Вместо этого используй "
        "«зона роста» или «точка внимания». "
        "Подпишись как Люм. В конце analysis — один открытый вопрос.\n\n"
        "Верни ТОЛЬКО JSON-объект без markdown-ограждений:\n"
        '{"analysis": "...", "temperature_score": 7, "match_count": 3}\n'
        "temperature_score — целое 0–10: «температура отношений» на этом квизе. "
        "0–3 холодно/напряжение; 4–6 нейтрально, есть что обсудить; "
        "7–8 тепло, хорошее понимание; 9–10 очень тепло, сильная связь. "
        "match_count — сколько вопросов из всех, где оба выбрали одинаковый вариант "
        "или оба открытых ответа схожи по смыслу. "
        "Без ключей кроме analysis, temperature_score, match_count."
    )
    user = (
        f"Ответы пары на сегодняшний квиз ({question_count} вопросов):\n\n"
        f"{answers_block}"
    )

    empty_metrics: dict[str, Any] = {
        "temperature_score": None,
        "match_count": None,
        "question_count": question_count or None,
    }

    try:
        raw = await ai_service._chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.6,
            max_tokens=1200,
        )
        if not raw or len(raw.strip()) < 20:
            raise RuntimeError("пустой инсайт от модели")

        try:
            data = _extract_json_object(raw)
            analysis = str(data.get("analysis") or "").strip()
            temp = _clamp_int(data.get("temperature_score"), 0, 10, default=None)
            match = _clamp_int(
                data.get("match_count"),
                0,
                max(question_count, 1),
                default=None,
            )
        except Exception:
            # Fallback: старый текстовый ответ без JSON
            analysis = raw.strip()
            temp = None
            match = local_matches

        if not analysis or len(analysis) < 30:
            raise RuntimeError("слишком короткий analysis")
        if not analysis.startswith("💡"):
            analysis = f"💡 Наши инсайты\n\n{analysis}"

        text = ai_service.as_lumen(ai_service.with_open_question(analysis))
        metrics = {
            "temperature_score": temp if temp is not None else 5,
            "match_count": match if match is not None else local_matches,
            "question_count": question_count,
        }
        return text, metrics, True
    except Exception as exc:
        logger.exception("analyze_daily_quiz failed: %s", exc)
        print(f"[Lumen DEBUG] daily analysis ERROR: {exc}", flush=True)
        return (
            ai_service.as_lumen(
                "🌿 Люм немного задумался... Давай попробуем собрать инсайт "
                "по квизу чуть позже."
            ),
            empty_metrics,
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
