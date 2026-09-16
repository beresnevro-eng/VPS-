"""
Сервис общения с LLM (DeepSeek / Groq, OpenAI-compatible).
Голос: Люм / проект «Шёпот» (см. MANIFESTO.md).
"""

from __future__ import annotations

import json
import logging
import random
import re
from typing import Any

import httpx

import config

logger = logging.getLogger(__name__)

# Ленивый AsyncOpenAI-клиент DeepSeek (не падаем при импорте без ключа/пакета)
_deepseek_client = None

# Метка голоса Люма (отличать от системных сообщений)
LUMEN_MARKS: tuple[str, ...] = ("🌿", "✨")

# Заголовки инсайтов — ротация вместо «Разбор / Анализ»
INSIGHT_TITLES: tuple[str, ...] = (
    "💡 Наши инсайты",
    "🌱 Взгляд со стороны",
    "❤️ Сердечный разговор",
    "✨ Что я заметил",
)

OPEN_QUESTIONS: tuple[str, ...] = (
    "Что из этого отозвалось сильнее всего?",
    "О чём вам хочется поговорить друг с другом прямо сейчас?",
    "Какой маленький шаг вы готовы попробовать сегодня?",
    "Что было самым тёплым моментом в этих ответах?",
)

# Запасные вопросы, если AI недоступен
_FALLBACK_QUESTIONS: list[dict[str, Any]] = [
    {
        "question": "Какой жест заботы от партнёра вы цените больше всего?",
        "type": "multiple_choice",
        "options": [
            "Тёплые слова и комплименты",
            "Помощь по дому без просьб",
            "Время вместе без телефонов",
            "Небольшие сюрпризы и подарки",
        ],
        "psychological_construct": "Языки любви",
    },
    {
        "question": "Как вам комфортнее решать бытовые разногласия?",
        "type": "multiple_choice",
        "options": [
            "Сразу спокойно обсудить",
            "Взять паузу, потом вернуться",
            "Договориться о правилах заранее",
            "Разделить зоны ответственности",
        ],
        "psychological_construct": "Точки внимания",
    },
    {
        "question": "Что сейчас важнее для ощущения близости?",
        "type": "multiple_choice",
        "options": [
            "Эмоциональная поддержка",
            "Физическая нежность",
            "Совместные планы и мечты",
            "Юмор и лёгкость в общении",
        ],
        "psychological_construct": "Близость",
    },
    {
        "question": "Насколько вы сейчас чувствуете, что вас слышат?",
        "type": "multiple_choice",
        "options": [
            "Почти всегда",
            "Чаще да, чем нет",
            "Иногда бывает пусто",
            "Хочется больше внимания к словам",
        ],
        "psychological_construct": "Доверие",
    },
    {
        "question": "Напишите одно желание к партнёру на эту неделю — мягко и конкретно.",
        "type": "open_ended",
        "options": [],
        "psychological_construct": "Потребности",
    },
]


def _brevity_instruction() -> str:
    """Жёсткая экономия токенов (особенно для DeepSeek с малым балансом)."""
    if config.effective_ai_provider() != "deepseek":
        return ""
    return (
        "Отвечай максимально емко. Не более 150-200 слов. Не лей воду. "
        "Пиши только суть. Если данных мало, сделай выводы на основе того, что есть. "
    )


def voice_system_preamble() -> str:
    """Строгая инструкция голоса Люма для всех системных промптов (MANIFESTO)."""
    return (
        f"Ты — {config.ASSISTANT_NAME}, мудрый и эмпатичный AI-помощник проекта "
        f"«{config.PROJECT_NAME}». "
        "Твой стиль: теплый, поддерживающий друг. "
        "Ты никогда не осуждаешь, не ставишь диагнозы и не занимаешь сторону "
        "одного из партнеров. "
        "Используй слова «зона роста», «инсайт», «точка внимания». "
        "Избегай клинических терминов. "
        "Говори от лица «мы» и «ваша связь». "
        f"Подписывай ответы от лица {config.ASSISTANT_NAME} "
        "(например: «Я, Люм, заметил…»), а не безликого ассистента. "
        "Не используй слова «проблема», «диагноз», «разбор». "
        f"{_brevity_instruction()}"
    )


def random_lumen_mark() -> str:
    return random.choice(LUMEN_MARKS)


def as_lumen(text: str) -> str:
    """Визуальная метка сообщений Люма в начале текста."""
    body = (text or "").strip()
    if not body:
        return f"{random_lumen_mark()} "
    if body.startswith(LUMEN_MARKS):
        return body
    return f"{random_lumen_mark()} {body}"


def random_insight_title() -> str:
    return random.choice(INSIGHT_TITLES)


def random_open_question() -> str:
    return random.choice(OPEN_QUESTIONS)


def with_open_question(text: str) -> str:
    """Добавляет открытый вопрос в конец, если его ещё нет."""
    body = (text or "").rstrip()
    if body.endswith("?"):
        return body
    return f"{body}\n\n💬 {random_open_question()}"


def _get_deepseek_client():
    """AsyncOpenAI для DeepSeek. None, если ключа или пакета нет."""
    global _deepseek_client
    if not config.deepseek_configured():
        return None
    if _deepseek_client is not None:
        return _deepseek_client
    try:
        from openai import AsyncOpenAI
    except ImportError:
        logger.error("Пакет openai не установлен. pip install openai")
        return None
    _deepseek_client = AsyncOpenAI(
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL,
        timeout=60.0,
    )
    return _deepseek_client


def _chat_url() -> str:
    return f"{config.GROQ_BASE_URL.rstrip('/')}/chat/completions"


async def _chat_deepseek(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.7,
    max_tokens: int = 600,
) -> str:
    client = _get_deepseek_client()
    if client is None:
        raise RuntimeError("DeepSeek не настроен (нет DEEPSEEK_API_KEY или пакета openai)")
    # Жёсткий потолок под малый баланс
    max_tokens = min(max_tokens, 700)
    resp = await client.chat.completions.create(
        model=config.DEEPSEEK_MODEL,
        messages=messages,  # type: ignore[arg-type]
        temperature=temperature,
        max_tokens=max_tokens,
    )
    content = (resp.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("DeepSeek вернул пустой content")
    logger.info("DeepSeek OK model=%s chars=%s", config.DEEPSEEK_MODEL, len(content))
    return content


async def _chat_groq(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.7,
    max_tokens: int = 1800,
) -> str:
    """Запрос к Groq. Перебирает модели при 404."""
    if not config.groq_configured():
        raise RuntimeError("GROQ_API_KEY не задан")
    headers = {
        "Authorization": f"Bearer {config.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    models = []
    for m in (config.GROQ_MODEL, *config.GROQ_MODEL_FALLBACKS):
        if m and m not in models:
            models.append(m)

    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        for model in models:
            body: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            try:
                resp = await client.post(_chat_url(), headers=headers, json=body)
                if resp.status_code == 404:
                    logger.warning("Groq model 404: %s body=%s", model, resp.text[:300])
                    continue
                if resp.status_code >= 400:
                    logger.warning(
                        "Groq HTTP %s model=%s body=%s",
                        resp.status_code,
                        model,
                        resp.text[:500],
                    )
                    last_exc = httpx.HTTPStatusError(
                        f"Groq {resp.status_code}: {resp.text[:200]}",
                        request=resp.request,
                        response=resp,
                    )
                    continue
                data = resp.json()
                msg = data["choices"][0]["message"]
                content = (msg.get("content") or "").strip()
                if not content:
                    content = (msg.get("reasoning") or "").strip()
                if not content:
                    raise RuntimeError(f"Пустой content от модели {model}")
                logger.info("Groq OK model=%s chars=%s", model, len(content))
                return content
            except Exception as exc:
                last_exc = exc
                logger.warning("Groq error model=%s: %s", model, exc)
                continue
    if last_exc:
        raise last_exc
    raise RuntimeError("Все модели Groq недоступны")


async def _chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.7,
    max_tokens: int = 1800,
) -> str:
    """Единая точка вызова LLM: DeepSeek или Groq по effective_ai_provider()."""
    provider = config.effective_ai_provider()
    logger.info("AI provider=%s (wanted=%s)", provider, config.AI_PROVIDER)

    if provider == "deepseek":
        try:
            # Для DeepSeek режем max_tokens по умолчанию
            return await _chat_deepseek(
                messages,
                temperature=temperature,
                max_tokens=min(max_tokens, 700),
            )
        except Exception as exc:
            logger.warning("DeepSeek failed: %s — пробуем Groq fallback", exc)
            if config.groq_configured():
                return await _chat_groq(
                    messages, temperature=temperature, max_tokens=max_tokens
                )
            raise

    try:
        return await _chat_groq(
            messages, temperature=temperature, max_tokens=max_tokens
        )
    except Exception as exc:
        logger.warning("Groq failed: %s — пробуем DeepSeek fallback", exc)
        if config.deepseek_configured():
            return await _chat_deepseek(
                messages,
                temperature=temperature,
                max_tokens=min(max_tokens, 700),
            )
        raise

def _extract_json_array(raw: str) -> list[Any]:
    """Достаёт JSON-массив из ответа модели (даже с markdown-обёрткой)."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("В ответе AI нет JSON-массива")
    arr = json.loads(text[start : end + 1])
    if not isinstance(arr, list):
        raise ValueError("Ожидался JSON-массив")
    return arr


def _normalize_questions(arr: list[Any]) -> list[dict[str, Any]]:
    """Приводит сырой JSON к единому формату вопросов."""
    out: list[dict[str, Any]] = []
    for item in arr:
        if isinstance(item, str):
            out.append(
                {
                    "question": item.strip(),
                    "type": "open_ended",
                    "options": [],
                    "psychological_construct": "",
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        q_text = str(item.get("question") or item.get("text") or "").strip()
        if not q_text:
            continue
        q_type = str(item.get("type") or "open_ended").strip().lower()
        if q_type not in ("multiple_choice", "open_ended"):
            q_type = "open_ended"
        options = item.get("options") or []
        if not isinstance(options, list):
            options = []
        options = [str(o).strip() for o in options if str(o).strip()][:4]
        if q_type == "multiple_choice" and len(options) < 2:
            q_type = "open_ended"
            options = []
        construct = str(item.get("psychological_construct") or item.get("construct") or "").strip()
        out.append(
            {
                "question": q_text,
                "type": q_type,
                "options": options,
                "psychological_construct": construct[:120],
            }
        )
    if len(out) < 5:
        raise ValueError(f"Нужно 5 вопросов, получено {len(out)}")
    mc = [q for q in out if q["type"] == "multiple_choice"]
    oe = [q for q in out if q["type"] == "open_ended"]
    normalized = (mc[:4] + oe[:1] + out)[:5]
    if not any(q["type"] == "open_ended" for q in normalized):
        normalized[-1]["type"] = "open_ended"
        normalized[-1]["options"] = []
    return normalized[:5]


async def generate_questions(
    topic: str | None = None,
    blocked_topics: list | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Генерирует 5 вопросов для пары (4 multiple_choice + 1 open_ended).
    blocked_topics — коды тем (routine, fun, …), которые нельзя трогать.
    Возвращает (тема, список объектов-вопросов).
    """
    blocked = [str(c).strip() for c in (blocked_topics or []) if str(c).strip()]
    blocked_labels = []
    for code in blocked:
        blocked_labels.append(f"{code} ({config.mood_label(code)})")
    blocked_txt = ", ".join(blocked_labels) if blocked_labels else "нет"

    if not topic:
        pool = [
            config.mood_prompt(c)
            for c in config.concrete_mood_codes()
            if c not in blocked
        ]
        topic = random.choice(pool) if pool else random.choice(config.DEFAULT_TOPICS)

    forbid = ""
    if blocked:
        forbid = (
            f"Категорически запрещено задавать вопросы на следующие темы: {blocked_txt}. "
            "Если тема заблокирована, выбери другую, даже если она не совпадает "
            "с текущим настроением. "
        )

    system_prompt = (
        f"{voice_system_preamble()} "
        "Сгенерируй ровно 5 вопросов для пары на заданную тему. "
        f"{forbid}"
        "Требование: 4 вопроса type=multiple_choice (по 4 эмпатичных варианта ответа) "
        "и 1 вопрос type=open_ended (без options). "
        "Варианты ответов — реалистичные, без стыда и осуждения. "
        "Верни строго JSON-массив объектов вида:\n"
        '{"question":"...","type":"multiple_choice","options":["...","...","...","..."],'
        '"psychological_construct":"Язык любви"}\n'
        "Без пояснений вне JSON."
    )
    user = (
        f"Тема: {topic}. "
        "Все тексты на русском. Только JSON. "
        f"Не затрагивай заблокированные темы: {blocked_txt}."
    )

    try:
        raw = await _chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user},
            ],
            temperature=0.8,
            max_tokens=1800,
        )
        questions = _normalize_questions(_extract_json_array(raw))
        return topic, questions
    except Exception as exc:
        logger.warning("AI генерация вопросов не удалась: %s — fallback", exc)
        return topic, [dict(q) for q in _FALLBACK_QUESTIONS]


async def analyze_couple_answers(payload: dict) -> str:
    """Мягкий инсайт по ответам пары (голос Люма)."""
    lines = [f"Тема квиза: {payload.get('topic', '—')}", ""]
    for i, q in enumerate(payload.get("questions", []), start=1):
        construct = q.get("psychological_construct") or ""
        qtype = q.get("type") or ""
        extra = f" [{construct}]" if construct else ""
        lines.append(f"Вопрос {i}{extra} ({qtype}): {q['text']}")
        for ans in q.get("answers", []):
            opt = ans.get("selected_option")
            if ans.get("skipped"):
                lines.append(f"  — {ans['name']}: пропущено")
            elif opt:
                lines.append(f"  — {ans['name']}: выбрал(а) «{opt}»")
            else:
                lines.append(f"  — {ans['name']}: {ans['text']}")
        lines.append("")
    answers_block = "\n".join(lines)

    system = (
        f"{voice_system_preamble()} "
        "Сравни ответы пары. Найди 1–2 совпадения и 1–2 различия. "
        "Дай мягкую рекомендацию на сегодня. Пиши на русском с эмодзи. "
        "В конце — один открытый вопрос."
    )
    user = f"Ответы пары на ежедневный квиз:\n\n{answers_block}"

    try:
        text = await _chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.6,
            max_tokens=1400,
        )
        return as_lumen(with_open_question(text))
    except Exception as exc:
        logger.exception("AI инсайт не удался: %s", exc)
        return as_lumen(
            with_open_question(
                "Сегодня полный инсайт не собрался, но вы уже сделали важный шаг — "
                "поделились ответами друг с другом.\n\n"
                "Заметьте, что совпало и что удивило — и какой один маленький жест "
                "заботы вы можете сделать друг для друга сегодня."
            )
        )


async def discuss_with_psychologist(user_message: str, quiz_context: str = "") -> str:
    """Короткий ответ Люма в режиме «Обсудить»."""
    system = (
        f"{voice_system_preamble()} "
        "Отвечай коротко (до 120 слов), по-русски. "
        "Помогай паре мягко уточнять чувства и договариваться. "
        "В конце задай один открытый вопрос."
    )
    user = (
        f"Контекст квиза:\n{quiz_context[:1500]}\n\n"
        f"Сообщение партнёра: {user_message}"
    )
    try:
        text = await _chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.6,
            max_tokens=500,
        )
        return as_lumen(with_open_question(text))
    except Exception:
        logger.exception("discuss failed")
        return as_lumen(
            with_open_question(
                "Сейчас связь чуть капризничает. "
                "Попробуйте сказать партнёру одной фразой: «Мне важно, чтобы…»."
            )
        )


async def weekly_digest_text(summary_facts: str) -> str:
    """Запасной текстовый дайджест (основной путь — analytics.generate_weekly_digest)."""
    system = (
        f"{voice_system_preamble()} "
        "На основе фактов недели составь тёплую сводку на русском: "
        "что совпадало, где расходились, и ровно 3 коротких шага "
        "на следующую неделю. Без осуждения. В конце — открытый вопрос."
    )
    try:
        text = await _chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": summary_facts[:3000]},
            ],
            temperature=0.55,
            max_tokens=900,
        )
        return as_lumen(with_open_question(text))
    except Exception:
        logger.exception("digest failed")
        return as_lumen(
            with_open_question(
                "На этой неделе вы уделили время диалогу — это уже ценность.\n"
                "Шаги: 1) 10 минут без телефонов 2) один жест заботы "
                "3) мягкий check-in вечером."
            )
        )


async def respond_to_digest_reflection(user_text: str) -> str:
    """Короткий отклик Люма на то, что отозвалось в дайджесте."""
    text = (user_text or "").strip()[:500]
    system = (
        f"{voice_system_preamble()} "
        "Дай короткий, поддерживающий отклик (1-2 предложения). "
        "Предложи один маленький шаг, как он может реализовать это на практике. "
        "Не будь навязчивым. Максимум 80 слов."
    )
    user = (
        "Пользователь ответил на вопрос о том, что ему отозвалось в дайджесте: "
        f"«{text}»."
    )
    try:
        reply = await _chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.55,
            max_tokens=220,
        )
        return as_lumen(reply)
    except Exception as exc:
        logger.exception("digest reflection failed: %s", exc)
        return as_lumen(
            "Слышу тебя. Один маленький шаг на сегодня — "
            "назвать это вслух партнёру одной мягкой фразой."
        )


async def generate_followup_questions(user_answers: dict) -> list[str]:
    """3 уточняющих вопроса после базовой анкеты."""
    compact = "\n".join(f"- {k}: {v}" for k, v in list(user_answers.items())[:20])
    system = (
        f"{voice_system_preamble()} "
        "Пользователь заполнил базовую анкету. "
        "Сгенерируй 3 глубоких, но бережных уточняющих вопроса, "
        "чтобы лучше понять его личность и скрытые потребности. "
        "Без травм-ярлыков. Верни строго JSON-массив из 3 строк на русском."
    )
    user = f"Базовая анкета:\n{compact[:2500]}"
    fallback = [
        "Что в отношениях чаще всего вызывает у вас внутреннее напряжение?",
        "Какой жест партнёра помогает вам быстрее почувствовать безопасность?",
        "О какой потребности вам пока сложно говорить вслух?",
    ]
    try:
        raw = await _chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.7,
            max_tokens=600,
        )
        arr = _extract_json_array(raw)
        questions = [str(q).strip() for q in arr if str(q).strip()][:3]
        while len(questions) < 3:
            questions.append(fallback[len(questions)])
        return questions[:3]
    except Exception as exc:
        logger.warning("followup questions failed: %s", exc)
        return fallback


# Сообщение при сбое Groq (портрет) — без «водянистого» фейк-анализа
PROFILE_API_SOFT_FAIL = (
    "🌿 Люм немного задумался... Давай попробуем собрать твой портрет "
    "чуть позже или начнем с ежедневного квиза."
)


def _format_qa_block(
    answers: dict[str, str],
    *,
    limit: int = 40,
    answer_max_len: int = 100,
) -> str:
    """Форматирует Q→A; длинные ответы обрезаем (экономия токенов)."""
    if not answers:
        return "(ответов пока нет)"
    lines: list[str] = []
    for k, v in list(answers.items())[:limit]:
        q = str(k)[:120]
        a = str(v).strip()
        if len(a) > answer_max_len:
            a = a[: answer_max_len - 1].rstrip() + "…"
        lines.append(f"- {q}: {a}")
    return "\n".join(lines)


async def generate_profile_summary(
    user_answers: dict,
    followup_answers: dict | None = None,
) -> tuple[str, bool]:
    """
    Глубокий персонализированный портрет от Люма.

    Returns:
        (text, ok) — ok=False при ошибке/таймауте API (портрет не готов).
    """
    followup_answers = followup_answers or {}
    base_answers = user_answers or {}

    # Обрезка ответов до 100 символов — экономия токенов DeepSeek
    base_block = _format_qa_block(base_answers, answer_max_len=100)
    fu_block = _format_qa_block(followup_answers, answer_max_len=100)

    logger.info(
        "generate_profile_summary DEBUG: provider=%s base=%s followup=%s",
        config.effective_ai_provider(),
        len(base_answers),
        len(followup_answers),
    )
    logger.info("generate_profile_summary DEBUG base_answers:\n%s", base_block[:3500])
    logger.info("generate_profile_summary DEBUG followup_answers:\n%s", fu_block[:2000])
    print(
        f"[Lumen DEBUG] provider={config.effective_ai_provider()} "
        f"profile payload: base={len(base_answers)} followup={len(followup_answers)}",
        flush=True,
    )
    print(f"[Lumen DEBUG] BASE:\n{base_block[:3000]}", flush=True)
    print(f"[Lumen DEBUG] FOLLOWUP:\n{fu_block[:1500]}", flush=True)

    system = (
        f"{voice_system_preamble()} "
        "Проанализируй ответы пользователя на анкету и уточняющие вопросы. "
        "Составь глубокий портрет (до 200 слов, лучше короче).\n"
        "Внутренне оцени по 4 осям (в тексте осями не называй):\n"
        "1) Ценности и приоритеты.\n"
        "2) Эмоциональные потребности (любовь).\n"
        "3) Стиль в конфликтах.\n"
        "4) Близость и быт.\n"
        "Структура ответа:\n"
        "- Сильные стороны (2-3 предложения).\n"
        "- Ключевая потребность в отношениях (1-2 предложения).\n"
        "- Зона роста (мягко).\n"
        "- Как партнёру лучше взаимодействовать с этим человеком.\n"
        "Местоимение «ты». Подпись от Люма. "
        "Если данных мало — предварительный портрет на основе того, что есть. "
        "Никогда не пиши «не удалось собрать портрет»."
    )
    user = (
        f"Ответы на анкету (база):\n{base_block}\n\n"
        f"Уточняющие вопросы:\n{fu_block}\n\n"
        "Составь портрет по структуре выше."
    )

    if not base_answers and not followup_answers:
        logger.warning("generate_profile_summary: пустые ответы — нечего анализировать")
        return PROFILE_API_SOFT_FAIL, False

    try:
        text = await _chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.5,
            max_tokens=550,
        )
        if not text or len(text.strip()) < 40:
            raise RuntimeError("AI вернул слишком короткий портрет")
        low = text.lower()
        if "не удалось собрать" in low:
            raise RuntimeError(f"AI вернул отказной текст: {text[:120]}")
        return as_lumen(with_open_question(text)), True
    except Exception as exc:
        logger.exception("profile summary failed: %s", exc)
        print(f"[Lumen DEBUG] profile AI ERROR: {exc}", flush=True)
        return PROFILE_API_SOFT_FAIL, False