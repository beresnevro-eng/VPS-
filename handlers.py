"""
Хендлеры aiogram 3: квиз с инлайн-кнопками, пропуском и обсуждением с AI.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

import ai_service
import analytics
import config
import onboarding as ob
from database import (
    Answer,
    Question,
    Quiz,
    UserProfile,
    both_answered_quiz,
    complete_profile,
    create_quiz_with_questions,
    ensure_partners,
    get_active_quiz,
    get_collecting_quiz,
    get_or_create_profile,
    get_pending_hidden_question,
    get_quiz_created_today,
    get_session,
    get_user_by_telegram,
    load_quiz_for_analysis,
    mark_hidden_question_used,
    mark_onboarding_done_pending_summary,
    pick_pending_hidden_for_couple,
    profile_answers_for_ai,
    profile_answers_split,
    profile_is_completed,
    save_answer,
    save_digest_reply,
    save_followup_answer,
    save_followup_questions,
    save_onboarding_answer,
    save_discussion_message,
    get_discussion_history,
    count_user_discussion_messages_24h,
    get_last_user_discussion_at,
    is_discussion_active,
    close_discussion,
    touch_last_quiz_sent,
    update_digest_reply_ai,
    add_blocked_topic,
    clear_blocked_topics,
    couple_blocked_topics,
)

logger = logging.getLogger(__name__)
router = Router()

# Ссылка на FSM storage — нужна scheduler'у / send_weekly_digest без Message.state
_fsm_storage: BaseStorage | None = None


class QuizFSM(StatesGroup):
    answering = State()
    discussing = State()


def _mood_keyboard() -> InlineKeyboardMarkup:
    """Выбор настроения для ручного квиза."""
    rows: list[list[InlineKeyboardButton]] = []
    codes = list(config.MOOD_CATALOG.keys())
    pair: list[InlineKeyboardButton] = []
    for code in codes:
        pair.append(
            InlineKeyboardButton(
                text=config.mood_label(code),
                callback_data=f"mood:{code}",
            )
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _block_topic_keyboard(topic_code: str) -> InlineKeyboardMarkup:
    code = (topic_code or "").strip()
    if not code or code == "surprise":
        return InlineKeyboardMarkup(inline_keyboard=[])
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚫 Не спрашивать больше на эту тему",
                    callback_data=f"block_topic:{code}",
                )
            ]
        ]
    )


def _insights_keyboard(quiz_id: int, topic_code: str = "") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="💬 Обсудить", callback_data=f"d:{quiz_id}")]]
    code = (topic_code or "").strip()
    if code and code != "surprise":
        rows.append(
            [
                InlineKeyboardButton(
                    text="🚫 Не спрашивать больше на эту тему",
                    callback_data=f"block_topic:{code}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pick_auto_topic_code(blocked: list[str]) -> str | None:
    pool = [c for c in config.concrete_mood_codes() if c not in set(blocked)]
    if not pool:
        return None
    return random.choice(pool)


async def _set_digest_waiting(bot: Bot, telegram_id: int, digest_id: int | None) -> None:
    """Ставит DigestStates.waiting_for_reply даже из scheduler (без Message)."""
    if _fsm_storage is None:
        logger.warning("FSM storage не инициализирован — диалог после дайджеста недоступен")
        return
    # bot.id может быть None до getMe — тогда StorageKey не совпадёт с апдейтами
    bot_id = bot.id
    if bot_id is None:
        me = await bot.get_me()
        bot_id = me.id
    key = StorageKey(bot_id=bot_id, chat_id=telegram_id, user_id=telegram_id)
    ctx = FSMContext(storage=_fsm_storage, key=key)
    await ctx.set_state(ob.DigestStates.waiting_for_reply)
    await ctx.update_data(digest_id=digest_id)
    logger.info(
        "DigestStates.waiting_for_reply set for user=%s digest_id=%s bot_id=%s",
        telegram_id,
        digest_id,
        bot_id,
    )

def _only_partners_user(user_id: int | None) -> bool:
    return user_id is not None and user_id in config.allowed_user_ids()


async def _show_main_menu(message: Message, text: str | None = None) -> None:
    name = config.partner_name(message.from_user.id)
    body = text or ai_service.as_lumen(
        f"Меню для {name}\n\n"
        f"«{ob.BTN_SHEPOT}» — пульт: история, портреты, внеочередной квиз.\n"
        f"«{ob.BTN_QUIZ}» — ежедневные/ручные вопросы в чате.\n"
        f"«{ob.BTN_STATUS}» — где вы сейчас.\n"
        f"«{ob.BTN_PROFILE}» — портрет от Люма.\n"
        f"«{ob.BTN_DIGEST}» — мягкие итоги недели."
    )
    await message.answer(body, reply_markup=ob.main_menu_keyboard(message.from_user.id))


START_GREETING = (
    "Привет! Я — Люм, твой помощник в проекте «Шёпот».\n"
    "Я здесь, чтобы помогать вам двоим лучше понимать друг друга. "
    "Я задаю вопросы, слушаю ваши ответы и мягко подсвечиваю то, "
    "что иногда остается за кадром.\n"
    "Давай начнем?"
)


async def _send_base_step(message: Message, state: FSMContext, step: int) -> None:
    q = ob.get_base_question(step)
    if not q:
        await _finish_base_and_followups(message, state)
        return
    await state.set_state(ob.OnboardingStates.base_question)
    await state.update_data(ob_step=step)
    await message.answer(
        ob.format_base_question(step, q, user_id=message.from_user.id if message.from_user else None),
        reply_markup=ob.question_keyboard(step, q),
    )


async def start_onboarding(
    message: Message,
    state: FSMContext,
    resume: bool = True,
    telegram_id: int | None = None,
) -> None:
    """Напоминание пройти анкету в Mini App (основной путь — Шёпот)."""
    tg_id = telegram_id or (message.from_user.id if message.from_user else None)
    if not tg_id:
        return
    await ensure_partners()
    async with get_session() as session:
        profile = await get_or_create_profile(session, tg_id)
        if profile.is_completed:
            return
        step = max(0, int(profile.onboarding_step or 0))
    await state.clear()
    total = ob.total_steps()
    if resume and step > 0:
        body = (
            f"Анкета ещё не закончена (шаг {min(step + 1, total)} из {total}).\n"
            "Открой «🌿 Открыть Шёпот» — продолжим с того же места."
        )
    else:
        body = (
            "Перед ежедневными квизами — короткая анкета в Шёпоте "
            f"({total} шагов: 15 вопросов + 3 уточнения от Люма).\n"
            "Открой «🌿 Открыть Шёпот» — ответы сохраняются, можно прерваться."
        )
    await message.answer(
        ai_service.as_lumen(body),
        reply_markup=ob.main_menu_keyboard(message.from_user.id),
    )


async def start_onboarding_in_chat(
    message: Message,
    state: FSMContext,
    resume: bool = True,
    telegram_id: int | None = None,
) -> None:
    """Запасной путь: анкета прямо в чате (/onboarding)."""
    tg_id = telegram_id or (message.from_user.id if message.from_user else None)
    if not tg_id:
        return
    await ensure_partners()
    step = 0
    async with get_session() as session:
        profile = await get_or_create_profile(session, tg_id)
        if resume and not profile.is_completed:
            step = max(0, int(profile.onboarding_step or 0))
            if step >= ob.base_total():
                await message.answer(
                    "Продолжаем уточняющие вопросы…",
                    reply_markup=ob.main_menu_keyboard(message.from_user.id),
                )
                await _resume_followups(message, state, profile, telegram_id=tg_id)
                return
    await message.answer(
        ai_service.as_lumen(
            "Перед ежедневными квизами — короткая анкета (15 вопросов).\n"
            "Это поможет Люму лучше понимать вас. Можно отвечать кнопками или текстом.\n"
            "Меню внизу уже доступно."
        ),
        reply_markup=ob.main_menu_keyboard(message.from_user.id),
    )
    await _send_base_step(message, state, step)


async def _resume_followups(
    message: Message,
    state: FSMContext,
    profile: UserProfile,
    telegram_id: int | None = None,
) -> None:
    import json

    try:
        questions = json.loads(profile.followup_json or "[]")
    except Exception:
        questions = []
    if not questions:
        await _finish_base_and_followups(message, state, telegram_id=telegram_id)
        return
    idx = max(0, int(profile.onboarding_step) - ob.base_total())
    if idx >= len(questions):
        await _finalize_profile(message, state, telegram_id=telegram_id)
        return
    await state.set_state(ob.OnboardingStates.followup_question)
    await state.update_data(fu_index=idx, fu_questions=questions, ob_user_id=telegram_id)
    await message.answer(ob.format_followup_question(idx, len(questions), questions[idx]))


async def _finish_base_and_followups(
    message: Message,
    state: FSMContext,
    telegram_id: int | None = None,
) -> None:
    tg_id = telegram_id or (message.from_user.id if message.from_user else None)
    if not tg_id:
        return
    await state.set_state(ob.OnboardingStates.generating_followups)
    await message.answer("✨ Базовая анкета готова. Готовлю 3 уточняющих вопроса…")
    async with get_session() as session:
        profile = await get_or_create_profile(session, tg_id)
        answers = profile_answers_for_ai(profile)
    questions = await ai_service.generate_followup_questions(answers)
    async with get_session() as session:
        await save_followup_questions(session, tg_id, questions)
        profile = await get_or_create_profile(session, tg_id)
        profile.onboarding_step = ob.base_total()
        await session.commit()
    await state.set_state(ob.OnboardingStates.followup_question)
    await state.update_data(fu_index=0, fu_questions=questions, ob_user_id=tg_id)
    await message.answer(ob.format_followup_question(0, len(questions), questions[0]))


async def _finalize_profile(
    message: Message,
    state: FSMContext,
    telegram_id: int | None = None,
) -> None:
    tg_id = telegram_id or (message.from_user.id if message.from_user else None)
    if not tg_id:
        return
    await state.set_state(ob.OnboardingStates.summarizing)
    await message.answer(ai_service.as_lumen("Собираю ваш портрет…"))

    async with get_session() as session:
        profile = await get_or_create_profile(session, tg_id)
        base_ans, fu_ans = profile_answers_split(profile)
        logger.info(
            "finalize_profile user=%s base=%s followup=%s raw_keys=%s",
            tg_id,
            len(base_ans),
            len(fu_ans),
            list((_load_raw_keys(profile))),
        )

    summary, public, private, ok = await ai_service.generate_profile_summary(
        base_ans, fu_ans, user_id=tg_id
    )

    async with get_session() as session:
        if ok:
            await complete_profile(
                session,
                tg_id,
                summary,
                ai_summary_public=public,
                ai_summary_private=private,
            )
        else:
            # Анкета пройдена (gating OK), портрет — pending для повтора
            await mark_onboarding_done_pending_summary(session, tg_id)

    await state.clear()
    if ok:
        await message.answer(
            ai_service.as_lumen(
                f"✅ Анкета завершена!\n\n👤 Ваш портрет:\n\n{summary[:3500]}"
            ),
            reply_markup=ob.main_menu_keyboard(message.from_user.id),
        )
        await message.answer(
            ai_service.as_lumen(
                "Можно начинать ежедневный квиз 👇\n"
                f"{ai_service.random_open_question()}"
            ),
            reply_markup=ob.main_menu_keyboard(message.from_user.id),
        )
    else:
        await message.answer(
            summary,  # уже PROFILE_API_SOFT_FAIL с 🌿
            reply_markup=ob.main_menu_keyboard(message.from_user.id),
        )


def _load_raw_keys(profile: UserProfile) -> list[str]:
    import json

    try:
        data = json.loads(profile.raw_answers_json or "{}")
        return list(data.keys()) if isinstance(data, dict) else []
    except Exception:
        return []


async def retry_profile_summary_if_pending(message: Message) -> bool:
    """Если анкета есть, а портрета нет — пробуем снова. True если обработали."""
    async with get_session() as session:
        profile = await get_or_create_profile(session, message.from_user.id)
        if not profile.is_completed or profile.ai_summary:
            return False
        base_ans, fu_ans = profile_answers_split(profile)

    await message.answer(ai_service.as_lumen("Снова собираю твой портрет…"))
    summary, public, private, ok = await ai_service.generate_profile_summary(
        base_ans, fu_ans, user_id=message.from_user.id
    )
    async with get_session() as session:
        if ok:
            await complete_profile(
                session,
                message.from_user.id,
                summary,
                ai_summary_public=public,
                ai_summary_private=private,
            )
        else:
            await mark_onboarding_done_pending_summary(session, message.from_user.id)

    if ok:
        await message.answer(
            ai_service.as_lumen(f"👤 Ваш портрет:\n\n{summary[:3500]}"),
            reply_markup=ob.main_menu_keyboard(message.from_user.id),
        )
    else:
        await message.answer(summary, reply_markup=ob.main_menu_keyboard(message.from_user.id))
    return True


async def _apply_base_answer(
    message: Message,
    state: FSMContext,
    step: int,
    answer_text: str,
    *,
    telegram_id: int | None = None,
) -> None:
    tg_id = telegram_id or (message.from_user.id if message.from_user else None)
    if not tg_id:
        return
    q = ob.get_base_question(step)
    if not q:
        # для followups нужен Message с from_user — подставим через fake
        if message.from_user is None:
            # создадим ответ в тот же чат
            class _U:
                id = tg_id
            message.from_user = _U()  # type: ignore[assignment]
        await _finish_base_and_followups(message, state)
        return
    next_step = step + 1
    async with get_session() as session:
        await save_onboarding_answer(
            session,
            tg_id,
            step=step,
            question_id=q["id"],
            question_text=q["text"],
            answer_text=answer_text,
            category=q["category"],
            next_step=next_step,
        )
    if message.from_user is None:
        class _U:
            id = tg_id
        message.from_user = _U()  # type: ignore[assignment]
    if next_step >= ob.base_total():
        await _finish_base_and_followups(message, state)
    else:
        await _send_base_step(message, state, next_step)


# Telegram обрезает текст Inline-кнопок (~30–64 символа) — при длинных вариантах
# показываем полный список в сообщении, а на кнопках только цифры.
INLINE_OPTION_MAX_LEN = 30


def _progress(index: int, total: int) -> str:
    return f"Вопрос {index + 1}/{total}"


def _options_need_number_buttons(options: list[str]) -> bool:
    """True, если хотя бы один вариант не влезет на кнопку целиком."""
    return any(len(str(opt)) > INLINE_OPTION_MAX_LEN for opt in options)


def _numbered_options_block(options: list[str]) -> str:
    """Нумерованный список вариантов для тела сообщения (длинные MC)."""
    return "\n".join(f"{i}. {opt}" for i, opt in enumerate(options, start=1))


def _mc_question_body(question_text: str, options: list[str]) -> str:
    """Текст MC-вопроса: при длинных вариантах — вопрос + нумерованный список."""
    text = (question_text or "").strip()
    if options and _options_need_number_buttons(options):
        return f"{text}\n\n{_numbered_options_block(options)}"
    return text


def _question_keyboard(question: Question) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if question.q_type == "multiple_choice":
        options = question.options_list()
        if _options_need_number_buttons(options):
            # Длинные варианты: кнопки 1 / 2 / 3 / 4; callback — индекс в options
            num_row = [
                InlineKeyboardButton(
                    text=str(i + 1),
                    callback_data=f"a:{question.id}:{i}",
                )
                for i in range(len(options))
            ]
            rows.append(num_row)
        else:
            for i, opt in enumerate(options):
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=opt,
                            callback_data=f"a:{question.id}:{i}",
                        )
                    ]
                )
    # Пропуск доступен для любого вопроса
    rows.append(
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"s:{question.id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _question_text(question: Question, index: int, total: int) -> str:
    construct = question.psychological_construct
    tag = f"\n🏷 {construct}" if construct else ""
    progress = f"{_progress(index, total)}{tag}"

    if question.q_type == "multiple_choice":
        options = question.options_list()
        body = _mc_question_body(question.text, options)
        if _options_need_number_buttons(options):
            hint = "\n\nНажмите цифру 👇 или «Пропустить», если вопрос неудобен."
        else:
            hint = (
                "\n\nВыберите вариант кнопкой 👇\n"
                "Или нажмите «Пропустить», если вопрос неудобен."
            )
        return f"{progress}\n\n{body}{hint}"

    hint = "\n\nНапишите ответ одним сообщением ✍️\nИли нажмите «Пропустить»."
    return f"{progress}\n\n{question.text}{hint}"


async def _send_question(bot: Bot, chat_id: int, question: Question, index: int, total: int) -> None:
    await bot.send_message(
        chat_id,
        _question_text(question, index, total),
        reply_markup=_question_keyboard(question),
    )


async def _resend_current_quiz_question(
    message: Message,
    *,
    question: Question,
    index: int,
    total: int,
) -> None:
    """Переспрашивает MC-вопрос с кнопками, если пользователь написал текст."""
    options = question.options_list() if question.q_type == "multiple_choice" else []
    body = _mc_question_body(question.text, options)
    intro = (
        "🌿 Похоже, ты хотел ответить текстом, но на этот вопрос нужно выбрать кнопку.\n"
        f"Вот вопрос ещё раз:\n\n{body}"
    )
    await message.answer(
        ai_service.as_lumen(intro),
        reply_markup=_question_keyboard(question),
    )


@router.message(CommandStart())
async def cmd_start(
    message: Message, state: FSMContext, command: CommandObject
) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        await message.answer(
            ai_service.as_lumen("Шёпот — только для нашей пары 🙂")
        )
        return
    await ensure_partners()
    # Всегда сбрасываем FSM, чтобы не застревать в answering/discussing
    await state.clear()

    args = (command.args or "").strip()
    if args.startswith("discuss_"):
        await _handle_discuss_deeplink(message, state, args)
        return
    if args.startswith("digest_"):
        await _handle_digest_deeplink(message, args)
        return
    if args in ("onboarding", "start_onboarding"):
        await start_onboarding(message, state, resume=False)
        return

    await message.answer(
        ai_service.as_lumen(START_GREETING),
        reply_markup=ob.main_menu_keyboard(message.from_user.id),
    )
    async with get_session() as session:
        done = await profile_is_completed(session, message.from_user.id)
    if not done:
        await start_onboarding(message, state, resume=True)
        return
    # Если портрет pending — пробуем снова при /start
    async with get_session() as session:
        profile = await get_or_create_profile(session, message.from_user.id)
        need_retry = profile.is_completed and not profile.ai_summary
    if need_retry:
        await retry_profile_summary_if_pending(message)
    await _show_main_menu(
        message,

        ai_service.as_lumen(
            f"С возвращением, {config.partner_name(message.from_user.id)}!\n"
            "Анкета уже пройдена. Нажми «Квиз», чтобы Люм задал вопросы на сегодня."
        ),
    )


async def _handle_digest_deeplink(message: Message, args: str) -> None:
    """Deep link digest_{id}: открыть Mini App (без длинного текста)."""
    raw = args.replace("digest_", "", 1).strip()
    try:
        digest_id = int(raw)
    except ValueError:
        digest_id = 0
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Открыть неделю в Шёпоте",
                    web_app=ob.mini_app_web_info_for(message.from_user.id),
                )
            ]
        ]
    )
    await message.answer(
        ai_service.as_lumen(
            "🌿 Неделя в обзоре ждёт в приложении."
            + (f" (#{digest_id})" if digest_id else "")
        ),
        reply_markup=kb,
    )


async def start_discussion_for_user(
    *,
    user_id: int,
    quiz_id: int,
    state: FSMContext,
    reply,
) -> None:
    """Общий старт/продолжение обсуждения (deeplink + callback). reply(text) — корутина отправки."""
    async with get_session() as session:
        quiz = await session.get(Quiz, quiz_id)
        if not quiz:
            await reply(ai_service.as_lumen("Этот квиз уже не доступен."))
            return
        analysis = (quiz.analysis_text or "").strip()
        topic = quiz.topic or ""
        topic_code = quiz.topic_code or ""
        created = quiz.created_at
        hist = await get_discussion_history(session, quiz_id, int(user_id), limit=6)
        active = await is_discussion_active(session, quiz_id, int(user_id))
        try:
            payload = await load_quiz_for_analysis(session, quiz_id)
        except Exception:
            payload = {"topic": topic, "questions": []}

    ctx_lines = [f"Тема: {payload.get('topic') or topic}"]
    for i, q in enumerate(payload.get("questions", []), 1):
        ctx_lines.append(f"{i}. {q['text']}")
        for a in q.get("answers", []):
            mark = " (пропуск)" if a.get("skipped") else ""
            val = a.get("selected_option") or a.get("text")
            ctx_lines.append(f"   {a.get('name')}: {val}{mark}")

    label = config.mood_label(topic_code) if topic_code else (topic or f"Квиз #{quiz_id}")
    try:
        date_s = (created or datetime.utcnow()).strftime("%d.%m.%Y")
    except Exception:
        date_s = ""

    await state.set_state(QuizFSM.discussing)
    await state.update_data(
        quiz_id=quiz_id,
        quiz_context="\n".join(ctx_lines)[:2000],
        discuss_topic=label,
        discuss_analysis=(analysis or "")[:2000],
    )

    if hist and active:
        preview_lines = []
        for m in hist[-3:]:
            who = "Люм" if m.role == "lum" else "Ты"
            preview_lines.append(f"{who}: {(m.text or '')[:160]}")
        preview = "\n".join(preview_lines)
        body = (
            f"Мы уже начали этот разговор по квизу «{label}». Продолжим?\n\n"
            f"{preview}\n\n"
            "Напиши, что думаешь дальше — или /done, чтобы закрыть."
        )
        await reply(ai_service.as_lumen(body))
        return

    brief = _brief_analysis(analysis)
    if brief:
        body = (
            f"🌿 Давай обсудим квиз «{label}» от {date_s}.\n"
            f"Вот что я заметил: {brief}\n"
            "Что тебя в этом зацепило больше всего?"
        )
    else:
        body = (
            f"🌿 Давай обсудим квиз «{label}» от {date_s}.\n"
            "Разбор ещё готовится, но мы можем поговорить о ваших ответах.\n"
            "Что тебя зацепило больше всего?"
        )
    async with get_session() as session:
        await save_discussion_message(
            session, quiz_id=quiz_id, user_id=int(user_id), role="lum", text=body
        )
    await reply(ai_service.as_lumen(body))


async def _handle_discuss_deeplink(
    message: Message, state: FSMContext, args: str
) -> None:
    """Deep link: t.me/Bot?start=discuss_{quiz_id}"""
    uid = message.from_user.id if message.from_user else None
    raw = args.replace("discuss_", "", 1).strip()
    try:
        quiz_id = int(raw)
    except ValueError:
        await message.answer(
            ai_service.as_lumen("Не нашёл этот квиз. Открой историю в Mini App ещё раз."),
            reply_markup=ob.main_menu_keyboard(uid),
        )
        return

    async def _reply(text: str) -> None:
        await message.answer(text, reply_markup=ob.main_menu_keyboard(uid))

    await start_discussion_for_user(
        user_id=int(uid), quiz_id=quiz_id, state=state, reply=_reply
    )


def _brief_analysis(text: str, max_sentences: int = 3, max_len: int = 420) -> str:
    """2–3 предложения из анализа для discuss-deeplink."""
    raw = (text or "").strip()
    if not raw:
        return ""
    # убрать лишние переносы
    compact = " ".join(raw.split())
    parts: list[str] = []
    buf = ""
    for ch in compact:
        buf += ch
        if ch in ".!?…" and len(buf.strip()) > 20:
            parts.append(buf.strip())
            buf = ""
            if len(parts) >= max_sentences:
                break
    if not parts:
        return compact[:max_len] + ("…" if len(compact) > max_len else "")
    out = " ".join(parts)
    if len(out) > max_len:
        return out[: max_len - 1].rstrip() + "…"
    return out


@router.message(Command("menu"))
@router.message(F.text == ob.BTN_MENU)
async def cmd_menu(message: Message) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    await _show_main_menu(message)


@router.message(Command("profile"))
@router.message(F.text == ob.BTN_PROFILE)
async def cmd_profile(message: Message) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    async with get_session() as session:
        profile = await get_or_create_profile(session, message.from_user.id)
        if not profile.is_completed:
            await message.answer(
                "Профиль ещё не готов. Напишите /start, чтобы продолжить анкету.",
                reply_markup=ob.main_menu_keyboard(message.from_user.id),
            )
            return
        if not profile.ai_summary:
            # Портрет pending — повторяем генерацию
            pass
        else:
            await message.answer(
                ai_service.as_lumen(
                    f"👤 Мой профиль\n\n{profile.ai_summary[:3800]}\n\n"
                    f"💬 {ai_service.random_open_question()}"
                ),
                reply_markup=ob.main_menu_keyboard(message.from_user.id),
            )
            return
    await retry_profile_summary_if_pending(message)


@router.message(Command("onboarding"))
async def cmd_onboarding_restart(message: Message, state: FSMContext) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    async with get_session() as session:
        from database import reset_onboarding

        await reset_onboarding(session, message.from_user.id)
    await state.clear()
    await start_onboarding_in_chat(message, state, resume=False)


@router.message(Command("help"))
@router.message(F.text == ob.BTN_HELP)
async def cmd_help(message: Message) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    await message.answer(
        ai_service.as_lumen(
            "Я — Люм, помощник проекта «Шёпот».\n\n"
            f"«{ob.BTN_SHEPOT}» — Mini App (история, портреты, темы, «хочу обсудить сейчас»).\n"
            f"«{ob.BTN_QUIZ}» — запустить квиз в чате.\n"
            f"«{ob.BTN_STATUS}» — где вы сейчас в квизе.\n"
            f"«{ob.BTN_PROFILE}» — ваш портрет от Люма.\n"
            f"«{ob.BTN_DIGEST}» — итоги недели.\n"
            f"«{ob.BTN_CANCEL}» или /cancel — сбросить текущий ввод/квиз/обсуждение.\n\n"
            "Как пользоваться:\n"
            "• Утром бот сам присылает квиз — отвечайте кнопками в чате.\n"
            "• Вне очереди — откройте Шёпот → «Хочу обсудить сейчас».\n"
            "• Вечером — Шёпот → История / Мы.\n\n"
            "/onboarding — анкета заново в чате (обычно — в Шёпоте)\n"
            "/reset_topics — снова открыть закрытые темы\n"
            "/cancel — сбросить состояние и вернуться в меню\n"
            "/done — закрыть обсуждение квиза\n\n"
            f"{ai_service.random_open_question()}"
        ),
        reply_markup=ob.main_menu_keyboard(message.from_user.id),
    )


@router.message(Command("cancel"))
@router.message(F.text == ob.BTN_CANCEL)
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    data = await state.get_data()
    quiz_id = data.get("quiz_id")
    cur = await state.get_state()
    if cur == QuizFSM.discussing.state and quiz_id:
        async with get_session() as session:
            await close_discussion(session, int(quiz_id), int(message.from_user.id))
    await state.clear()
    await message.answer(
        ai_service.as_lumen(
            "🌿 Хорошо, отменил. Если захочешь вернуться — /quiz или «Квиз»."
        ),
        reply_markup=ob.main_menu_keyboard(message.from_user.id),
    )


@router.message(Command("done"))
async def cmd_done(message: Message, state: FSMContext) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    cur = await state.get_state()
    data = await state.get_data()
    quiz_id = data.get("quiz_id")
    if cur != QuizFSM.discussing.state:
        await message.answer(
            ai_service.as_lumen("Сейчас нет активного обсуждения."),
            reply_markup=ob.main_menu_keyboard(message.from_user.id),
        )
        return
    bye = (
        "🌿 Спасибо за разговор. Если захочешь вернуться — "
        "просто открой квиз в Истории."
    )
    if quiz_id:
        async with get_session() as session:
            await save_discussion_message(
                session,
                quiz_id=int(quiz_id),
                user_id=int(message.from_user.id),
                role="lum",
                text=bye,
            )
            await close_discussion(session, int(quiz_id), int(message.from_user.id))
    await state.clear()
    await message.answer(
        ai_service.as_lumen(bye),
        reply_markup=ob.main_menu_keyboard(message.from_user.id),
    )


async def _quiz_progress_snapshot(session, quiz, telegram_id: int) -> tuple[str, dict]:
    """Текст прогресса + метрики для активного квиза."""
    qs = (
        await session.execute(
            select(Question).where(Question.quiz_id == quiz.id).order_by(Question.position)
        )
    ).scalars().all()
    total = len(qs)
    user = await get_user_by_telegram(session, telegram_id)
    done = 0
    if user:
        for q in qs:
            a = await session.scalar(
                select(Answer).where(Answer.question_id == q.id, Answer.user_id == user.id)
            )
            if a:
                done += 1

    partner_id = None
    for pid in config.partner_ids():
        if pid != telegram_id:
            partner_id = pid
            break
    partner_done = False
    if partner_id:
        from database import user_finished_quiz

        partner_done = await user_finished_quiz(session, quiz.id, partner_id)

    topic = ""
    if quiz.topic_code:
        topic = config.mood_label(quiz.topic_code)
    topic = topic or (quiz.topic or "—")

    partner_line = "да" if partner_done else "нет"
    body = (
        f"Квиз #{quiz.id} · Тема: {topic}\n"
        f"Твоих ответов: {done}/{total}\n"
        f"Партнёр ответил: {partner_line}"
    )
    meta = {
        "done": done,
        "total": total,
        "partner_done": partner_done,
        "topic": topic,
    }
    return body, meta


def _active_quiz_actions_keyboard(quiz_id: int, user_id: int) -> InlineKeyboardMarkup:
    """Кнопки: продолжить / Mini App / сбросить."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Продолжить сейчас",
                    callback_data=f"quiz:resume:{quiz_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Открыть Mini App",
                    web_app=ob.mini_app_web_info_for(user_id),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✕ Сбросить квиз",
                    callback_data=f"quiz:reset:{quiz_id}",
                )
            ],
        ]
    )


def _status_quiz_keyboard(quiz_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Продолжить",
                    callback_data=f"quiz:resume:{quiz_id}",
                ),
                InlineKeyboardButton(
                    text="📊 Mini App",
                    web_app=ob.mini_app_web_info_for(user_id),
                ),
            ]
        ]
    )


@router.message(Command("status"))
@router.message(F.text == ob.BTN_STATUS)
async def cmd_status(message: Message) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    async with get_session() as session:
        quiz = await get_active_quiz(session)
        if not quiz:
            await message.answer(
                "Активного квиза нет.",
                reply_markup=ob.main_menu_keyboard(message.from_user.id),
            )
            return
        body, _ = await _quiz_progress_snapshot(session, quiz, message.from_user.id)
    await message.answer(
        ai_service.as_lumen(body),
        reply_markup=_status_quiz_keyboard(quiz.id, message.from_user.id),
    )


@router.message(Command("digest"))
@router.message(F.text == ob.BTN_DIGEST)
async def cmd_digest(message: Message, bot: Bot, state: FSMContext) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    await message.answer("Собираю итоги недели…", reply_markup=ob.main_menu_keyboard(message.from_user.id))
    await send_weekly_digest(bot, only_chat=message.chat.id, state=state)


@router.message(Command("quiz"))
@router.message(F.text == ob.BTN_QUIZ)
async def cmd_quiz(message: Message, state: FSMContext, bot: Bot) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    async with get_session() as session:
        active = await get_active_quiz(session)
        if active and active.status == "collecting":
            body, _ = await _quiz_progress_snapshot(session, active, message.from_user.id)
            text = (
                "🌿 У вас уже есть открытый квиз на сегодня.\n\n"
                f"{body}\n\n"
                "Что делаем?"
            )
            await message.answer(
                ai_service.as_lumen(text),
                reply_markup=_active_quiz_actions_keyboard(active.id, message.from_user.id),
            )
            return
    await message.answer(
        ai_service.as_lumen(
            "Какое настроение у вас сейчас? Люм подберёт вопросы под него."
        ),
        reply_markup=_mood_keyboard(),
    )


@router.message(Command("reset_topics"))
async def cmd_reset_topics(message: Message) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    async with get_session() as session:
        await clear_blocked_topics(session, message.from_user.id)
    await message.answer(
        ai_service.as_lumen(
            "Хорошо. Все темы снова открыты — могу спрашивать обо всём, "
            "что помогает вашей связи."
        ),
        reply_markup=ob.main_menu_keyboard(message.from_user.id),
    )


@router.message(Command("test_notifications"))
async def cmd_test_notifications(message: Message, bot: Bot) -> None:
    """Отладка: три пуша сразу (квиз / реактивация / разбор)."""
    uid = message.from_user.id if message.from_user else None
    if not _only_partners_user(uid):
        return
    from scheduler import run_test_notifications  # noqa: WPS433

    await message.answer(ai_service.as_lumen("Отправляю тестовые пуши…"))
    try:
        await run_test_notifications(bot, int(uid))
        await message.answer(ai_service.as_lumen("Готово — три тестовых сообщения выше."))
    except Exception as exc:
        logger.exception("test_notifications: %s", exc)
        await message.answer(ai_service.as_lumen(f"Ошибка: {exc}"))


@router.message(Command("test_hidden_question"))
async def cmd_test_hidden_question(message: Message, bot: Bot) -> None:
    """Отладка: квиз с принудительным включением pending скрытого вопроса автора."""
    uid = message.from_user.id if message.from_user else None
    if not _only_partners_user(uid):
        return
    async with get_session() as session:
        pending = await get_pending_hidden_question(session, int(uid))
        blocked = await couple_blocked_topics(session)
    if not pending:
        await message.answer(
            ai_service.as_lumen(
                "У тебя нет активных скрытых вопросов. "
                "Добавь тему в Mini App → Мы → Скрытые."
            )
        )
        return
    code = _pick_auto_topic_code(blocked) or next(
        (c for c in config.concrete_mood_codes() if c not in blocked),
        None,
    )
    if not code:
        await message.answer(ai_service.as_lumen("Все темы закрыты — нечего запускать."))
        return
    await message.answer(
        ai_service.as_lumen(
            f"Запускаю тестовый квиз со скрытой темой «{pending.text}». "
            "Партнёр не увидит, что это твоя просьба."
        )
    )
    try:
        quiz = await start_quiz_for_pair(
            bot,
            code,
            automatic=False,
            notify_chat=message.chat.id,
            close_active=True,
            force_hidden_user_id=int(uid),
        )
        await message.answer(
            ai_service.as_lumen(f"Готово. Квиз #{quiz.id} разослан (скрытый вопрос использован).")
        )
    except RuntimeError as exc:
        if str(exc) == "active_quiz_exists":
            await message.answer(
                ai_service.as_lumen("Есть активный квиз — не удалось закрыть и перезапустить.")
            )
        else:
            raise
    except Exception as exc:
        logger.exception("test_hidden_question failed: %s", exc)
        await message.answer(ai_service.as_lumen(f"Не вышло: {exc}"))


@router.callback_query(F.data.startswith("mood:"))
async def on_mood_chosen(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not _only_partners_user(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    code = (callback.data or "").split(":", 1)[-1].strip().lower()
    if code not in config.MOOD_CATALOG:
        await callback.answer()
        return

    async with get_session() as session:
        active = await get_active_quiz(session)
        if active and active.status == "collecting":
            await callback.answer()
            await callback.message.answer(
                ai_service.as_lumen(
                    "У нас уже есть активный квиз на сегодня. "
                    "Давай сначала закончим его, а потом я задам новые вопросы."
                ),
                reply_markup=ob.main_menu_keyboard(callback.from_user.id),
            )
            return
        blocked = await couple_blocked_topics(session)

    if code != "surprise" and code in blocked:
        await callback.answer()
        await callback.message.answer(
            ai_service.as_lumen(
                "Ты просил не спрашивать об этом. Давай выберем что-то другое?"
            ),
            reply_markup=_mood_keyboard(),
        )
        return

    resolved = _pick_auto_topic_code(blocked) if code == "surprise" else code
    if not resolved:
        await callback.answer()
        await callback.message.answer(
            ai_service.as_lumen(
                "Похоже, почти все темы сейчас закрыты. "
                "Напиши /reset_topics, если хочешь открыть их снова."
            ),
            reply_markup=ob.main_menu_keyboard(callback.from_user.id),
        )
        return

    await callback.answer("Готовлю вопросы…")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        ai_service.as_lumen(f"Тема: {config.mood_label(resolved)}. Собираю вопросы…"),
        reply_markup=_block_topic_keyboard(resolved),
    )
    await start_daily_quiz(
        bot,
        topic=config.mood_prompt(resolved),
        topic_code=resolved,
        notify_chat=callback.message.chat.id,
        automatic=False,
    )


@router.callback_query(F.data.startswith("block_topic:"))
async def on_block_topic(callback: CallbackQuery) -> None:
    if not callback.from_user or not _only_partners_user(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    code = (callback.data or "").split(":", 1)[-1].strip().lower()
    if not code or code == "surprise" or code not in config.MOOD_CATALOG:
        await callback.answer("Эту тему нельзя закрыть", show_alert=True)
        return
    async with get_session() as session:
        await add_blocked_topic(session, callback.from_user.id, code)
    await callback.answer("Запомнил")
    if callback.message:
        await callback.message.answer(
            ai_service.as_lumen(
                "Понял. Больше не буду спрашивать об этом. "
                "Если передумаешь, напиши /reset_topics"
            ),
            reply_markup=ob.main_menu_keyboard(callback.from_user.id),
        )


@router.callback_query(F.data.startswith("o:"), ob.OnboardingStates.base_question)
async def onboarding_choice(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not _only_partners_user(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    try:
        step, opt_idx = int(parts[1]), int(parts[2])
    except ValueError:
        await callback.answer()
        return
    q = ob.get_base_question(step)
    if not q:
        await callback.answer()
        return
    options = q.get("options") or []
    if opt_idx < 0 or opt_idx >= len(options):
        await callback.answer("Неверный вариант", show_alert=True)
        return
    chosen = options[opt_idx]
    try:
        display = q.get("text") or ""
        tmpl = q.get("text_template")
        if tmpl and callback.from_user:
            display = str(tmpl).format(
                name=config.partner_name(callback.from_user.id) or "ты"
            )
        await callback.message.edit_text(
            f"📋 Анкета {step + 1}/{ob.base_total()}\n\n{display}\n\n✅ {chosen}",
            reply_markup=None,
        )
    except Exception:
        pass
    await callback.answer("Принято")
    await _apply_base_answer(
        callback.message, state, step, chosen, telegram_id=callback.from_user.id
    )


@router.message(ob.OnboardingStates.base_question, F.text)
async def onboarding_text_answer(message: Message, state: FSMContext) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    if message.text in ob.MENU_BUTTON_TEXTS:
        return
    data = await state.get_data()
    step = int(data.get("ob_step", 0))
    q = ob.get_base_question(step)
    if not q:
        await _finish_base_and_followups(message, state)
        return
    if q.get("type") == "multiple_choice":
        await message.answer("Выберите вариант кнопкой под вопросом 👆")
        return
    await _apply_base_answer(message, state, step, message.text.strip())


@router.message(ob.OnboardingStates.followup_question, F.text)
async def onboarding_followup_answer(message: Message, state: FSMContext) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    if message.text in ob.MENU_BUTTON_TEXTS:
        return
    data = await state.get_data()
    idx = int(data.get("fu_index", 0))
    questions: list[str] = data.get("fu_questions") or []
    if not questions or idx >= len(questions):
        await _finalize_profile(message, state)
        return
    next_step = ob.base_total() + idx + 1
    async with get_session() as session:
        await save_followup_answer(
            session,
            message.from_user.id,
            index=idx,
            question_text=questions[idx],
            answer_text=message.text.strip(),
            next_step=next_step,
        )
    nxt = idx + 1
    if nxt >= len(questions):
        await _finalize_profile(message, state)
        return
    await state.update_data(fu_index=nxt)
    await message.answer(ob.format_followup_question(nxt, len(questions), questions[nxt]))


async def start_quiz_for_pair(
    bot: Bot,
    mood_code: str,
    *,
    automatic: bool = False,
    notify_chat: int | None = None,
    close_active: bool = False,
    force_hidden_user_id: int | None = None,
    send_to: list[int] | None = None,
) -> Quiz:
    """
    Ядро запуска квиза для пары (Mini App API + бот + scheduler).

    - Проверяет/закрывает активный квиз по флагу close_active.
    - Генерирует вопросы, сохраняет Quiz, рассылает получателям.
    - send_to: кому отправить сейчас (по умолчанию — обоим). Остальные могут получить позже.
    - С ~60% вероятностью включает pending скрытый вопрос партнёра.
    - force_hidden_user_id — принудительно взять pending этого юзера (для /test_hidden_question).
    - Возвращает созданный Quiz.
    """
    code = (mood_code or "").strip().lower()
    if not code or code not in config.MOOD_CATALOG:
        raise ValueError(f"Неизвестный mood_code: {mood_code}")
    if code == "surprise":
        raise ValueError("surprise нужно разрешить до вызова start_quiz_for_pair")

    await ensure_partners()
    async with get_session() as session:
        blocked = await couple_blocked_topics(session)
        active = await get_collecting_quiz(session)
        if active:
            if close_active:
                active.status = "closed"
                await session.commit()
            else:
                raise RuntimeError("active_quiz_exists")

        hidden_row = None
        if force_hidden_user_id:
            hidden_row = await get_pending_hidden_question(session, int(force_hidden_user_id))
        elif random.random() < 0.60:
            hidden_row = await pick_pending_hidden_for_couple(
                session, list(config.partner_ids())
            )
        hidden_id = hidden_row.id if hidden_row else None
        hidden_text = (hidden_row.text or "").strip() if hidden_row else None

    if code in blocked:
        raise ValueError("blocked_topic")

    topic = config.mood_prompt(code)
    topic, questions = await ai_service.generate_questions(
        topic,
        blocked_topics=blocked,
        hidden_question=hidden_text,
    )

    recipients = list(send_to) if send_to is not None else list(config.partner_ids())

    async with get_session() as session:
        quiz = await create_quiz_with_questions(
            session, topic, questions, topic_code=code
        )
        if hidden_id:
            await mark_hidden_question_used(
                session, question_id=hidden_id, quiz_id=quiz.id
            )
            logger.info(
                "Hidden question #%s embedded into quiz #%s (no author leak)",
                hidden_id,
                quiz.id,
            )
        q_rows = (
            await session.execute(
                select(Question).where(Question.quiz_id == quiz.id).order_by(Question.position)
            )
        ).scalars().all()
        quiz_id = quiz.id
        total = len(q_rows)
        first = q_rows[0] if q_rows else None
        quiz_topic = quiz.topic
        quiz_code = quiz.topic_code or code
        quiz_status = quiz.status
        quiz_created = quiz.created_at

    label = config.mood_label(code)
    for tg_id in recipients:
        try:
            await deliver_quiz_to_user(
                bot,
                tg_id,
                quiz_id=quiz_id,
                label=label,
                first=first,
                total=total,
                topic_code=code,
                automatic=automatic,
            )
        except Exception as exc:
            logger.warning("send %s: %s", tg_id, exc)

    if notify_chat and notify_chat not in config.partner_ids():
        try:
            await bot.send_message(
                notify_chat,
                f"Квиз #{quiz_id} создан (отправили: {recipients}).",
            )
        except Exception as exc:
            logger.warning("notify_chat %s: %s", notify_chat, exc)

    out = Quiz(
        id=quiz_id,
        topic=quiz_topic,
        topic_code=quiz_code,
        status=quiz_status,
        created_at=quiz_created,
    )
    return out


async def deliver_quiz_to_user(
    bot: Bot,
    tg_id: int,
    *,
    quiz_id: int,
    label: str,
    first: Question | None,
    total: int,
    topic_code: str,
    automatic: bool = False,
) -> None:
    """Отправляет уже созданный квиз одному партнёру и ставит last_quiz_sent_at."""
    if automatic:
        intro = ai_service.as_lumen(
            f"Доброе утро! Вот твой ежедневный квиз. "
            f"Сегодня Люм подготовил вопросы на тему: {label}."
        )
    else:
        intro = ai_service.as_lumen(
            f"💌 Квиз #{quiz_id}\nТема: {label}\nВопросов: {total}"
        )
    kb = _block_topic_keyboard(topic_code)
    await bot.send_message(
        tg_id,
        intro,
        reply_markup=kb if kb.inline_keyboard else None,
    )
    if first:
        await _send_question(bot, tg_id, first, 0, total)
    async with get_session() as session:
        await touch_last_quiz_sent(session, tg_id)
    logger.info("push type=quiz_delivery user_id=%s quiz_id=%s", tg_id, quiz_id)


async def deliver_existing_quiz_to_user(bot: Bot, quiz: Quiz, tg_id: int) -> None:
    """Добрасывает сегодняшний квиз партнёру в его preferred_hour."""
    async with get_session() as session:
        q_rows = (
            await session.execute(
                select(Question)
                .where(Question.quiz_id == quiz.id)
                .order_by(Question.position)
            )
        ).scalars().all()
        first = q_rows[0] if q_rows else None
        total = len(q_rows)
        code = quiz.topic_code or ""
        label = config.mood_label(code) if code in config.MOOD_CATALOG else (quiz.topic or "квиз")
        quiz_id = quiz.id
    await deliver_quiz_to_user(
        bot,
        tg_id,
        quiz_id=quiz_id,
        label=label,
        first=first,
        total=total,
        topic_code=code,
        automatic=True,
    )


async def start_daily_quiz(
    bot: Bot,
    topic: str | None = None,
    notify_chat: int | None = None,
    *,
    automatic: bool = False,
    topic_code: str | None = None,
) -> Quiz | None:
    """
    automatic=True — плановый квиз в 10:00, без выбора настроения.
    automatic=False — ручной, тема уже выбрана.
    Обёртка над start_quiz_for_pair.
    """
    await ensure_partners()
    async with get_session() as session:
        blocked = await couple_blocked_topics(session)

    if automatic:
        resolved_code = _pick_auto_topic_code(blocked)
        if not resolved_code:
            for tg_id in config.partner_ids():
                try:
                    await bot.send_message(
                        tg_id,
                        ai_service.as_lumen(
                            "Сегодня почти все темы закрыты. "
                            "Напишите /reset_topics, если хотите снова открыть их."
                        ),
                    )
                except Exception as exc:
                    logger.warning("blocked-all %s: %s", tg_id, exc)
            return None
        return await start_quiz_for_pair(
            bot,
            resolved_code,
            automatic=True,
            notify_chat=notify_chat,
            close_active=True,
        )

    resolved_code = (topic_code or "").strip().lower()
    if not resolved_code:
        # legacy: topic строкой без кода
        if topic:
            # подберём код по prompt или оставим deep
            for code, meta in config.MOOD_CATALOG.items():
                if code == "surprise":
                    continue
                if meta.get("prompt") and meta["prompt"] in topic:
                    resolved_code = code
                    break
        if not resolved_code:
            resolved_code = "deep"

    return await start_quiz_for_pair(
        bot,
        resolved_code,
        automatic=False,
        notify_chat=notify_chat,
        close_active=False,
    )


async def _advance_after_answer(
    bot: Bot,
    user_id: int,
    chat_id: int,
    state: FSMContext,
    quiz_id: int,
) -> None:
    async with get_session() as session:
        qs = (
            await session.execute(
                select(Question).where(Question.quiz_id == quiz_id).order_by(Question.position)
            )
        ).scalars().all()
        user = await get_user_by_telegram(session, user_id)
        next_q = None
        next_idx = 0
        for i, q in enumerate(qs):
            a = None
            if user:
                a = await session.scalar(
                    select(Answer).where(Answer.question_id == q.id, Answer.user_id == user.id)
                )
            if a is None:
                next_q = q
                next_idx = i
                break
        done = await both_answered_quiz(session, quiz_id)
        total = len(qs)

    if next_q is not None:
        if next_q.q_type == "open_ended":
            await state.set_state(QuizFSM.answering)
            await state.update_data(quiz_id=quiz_id, q_ids=[q.id for q in qs], index=next_idx)
        else:
            # для MC FSM не обязателен, но сбрасываем discussing
            cur = await state.get_state()
            if cur == QuizFSM.discussing.state:
                pass
            else:
                await state.clear()
        await _send_question(bot, chat_id, next_q, next_idx, total)
        return

    await state.clear()
    if done:
        await bot.send_message(
            chat_id,
            "Готовлю сравнение и разбор…"
            if not config.is_solo_mode()
            else "Готовлю разбор (соло-тест)…",
        )
        await finish_quiz(bot, quiz_id)
    else:
        await bot.send_message(chat_id, "Все ваши ответы записаны ✅ Ждём партнёра…")
        # Критичный пуш второму партнёру (ежедневный и внеочередной квиз)
        if not config.is_solo_mode():
            await _notify_partner_waiting(bot, finished_user_id=user_id, quiz_id=quiz_id)


async def _notify_partner_waiting(
    bot: Bot, *, finished_user_id: int, quiz_id: int
) -> None:
    """Пуш: один партнёр закончил — второму пора ответить (без кнопок)."""
    from gender_utils import gendered

    name = config.partner_name(finished_user_id) or "Партнёр"
    answered = gendered(
        finished_user_id,
        "ответил",
        "ответила",
        "ответил(а)",
    )
    text = (
        f"🌿 {name} {answered} на квиз ❤️\n"
        "Открой чат и ответь — тогда Люм сравнит ваши ответы."
    )
    for tg_id in config.partner_ids():
        if tg_id == finished_user_id:
            continue
        try:
            await bot.send_message(tg_id, text)
        except Exception as exc:
            logger.warning("partner-wait notify %s (quiz %s): %s", tg_id, quiz_id, exc)


@router.callback_query(F.data.startswith("quiz:resume:"))
async def on_quiz_resume(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not _only_partners_user(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    try:
        quiz_id = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer()
        return

    async with get_session() as session:
        quiz = await session.get(Quiz, quiz_id)
        if not quiz or quiz.status != "collecting":
            await callback.answer("Квиз уже закрыт", show_alert=True)
            return
        user = await get_user_by_telegram(session, callback.from_user.id)
        qs = (
            await session.execute(
                select(Question).where(Question.quiz_id == quiz.id).order_by(Question.position)
            )
        ).scalars().all()
        next_q = None
        next_idx = 0
        for i, q in enumerate(qs):
            a = None
            if user:
                a = await session.scalar(
                    select(Answer).where(Answer.question_id == q.id, Answer.user_id == user.id)
                )
            if a is None:
                next_q = q
                next_idx = i
                break
        total = len(qs)

    await callback.answer()
    if next_q is None:
        from gender_utils import gendered

        done_verb = gendered(
            callback.from_user.id,
            "ответил",
            "ответила",
            "ответил(а)",
        )
        await callback.message.answer(
            ai_service.as_lumen(
                f"Ты уже {done_verb} на все вопросы этого квиза. Ждём партнёра или разбор."
            ),
            reply_markup=ob.main_menu_keyboard(callback.from_user.id),
        )
        return

    if next_q.q_type == "open_ended":
        await state.set_state(QuizFSM.answering)
        await state.update_data(
            quiz_id=quiz_id, q_ids=[q.id for q in qs], index=next_idx
        )
    else:
        await state.clear()
    await _send_question(bot, callback.message.chat.id, next_q, next_idx, total)


@router.callback_query(F.data.startswith("quiz:reset:"))
async def on_quiz_reset_ask(callback: CallbackQuery) -> None:
    if not callback.from_user or not _only_partners_user(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    try:
        quiz_id = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    # quiz:reset:ID — не путать с quiz:reset_yes
    if len(parts) != 3:
        await callback.answer()
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, сбросить",
                    callback_data=f"quiz:reset_yes:{quiz_id}",
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=f"quiz:reset_no:{quiz_id}",
                ),
            ]
        ]
    )
    await callback.answer()
    await callback.message.answer(
        ai_service.as_lumen(
            "Сбросить квиз? Он закроется для обоих, ответы сохранятся в истории."
        ),
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("quiz:reset_no:"))
async def on_quiz_reset_no(callback: CallbackQuery) -> None:
    if not callback.from_user or not _only_partners_user(callback.from_user.id):
        await callback.answer()
        return
    await callback.answer("Ок, оставляем")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@router.callback_query(F.data.startswith("quiz:reset_yes:"))
async def on_quiz_reset_yes(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not _only_partners_user(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    try:
        quiz_id = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer()
        return

    async with get_session() as session:
        quiz = await session.get(Quiz, quiz_id)
        if not quiz:
            await callback.answer("Квиз не найден", show_alert=True)
            return
        if quiz.status in ("closed", "analyzed"):
            await callback.answer("Уже закрыт")
            return
        quiz.status = "closed"
        await session.commit()

    await state.clear()
    await callback.answer("Сброшено")
    await callback.message.answer(
        ai_service.as_lumen("Квиз закрыт. Можешь начать новый — /quiz"),
        reply_markup=ob.main_menu_keyboard(callback.from_user.id),
    )
    # уведомить партнёра
    for tg_id in config.partner_ids():
        if tg_id == callback.from_user.id:
            continue
        try:
            await callback.bot.send_message(
                tg_id,
                ai_service.as_lumen(
                    f"Партнёр закрыл квиз #{quiz_id}. Можно начать новый — /quiz"
                ),
                reply_markup=ob.main_menu_keyboard(tg_id),
            )
        except Exception as exc:
            logger.warning("quiz reset notify %s: %s", tg_id, exc)


@router.callback_query(F.data.startswith("a:"))
async def on_choice(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not _only_partners_user(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    try:
        qid, opt_idx = int(parts[1]), int(parts[2])
    except ValueError:
        await callback.answer()
        return

    q_text = ""
    async with get_session() as session:
        question = await session.get(Question, qid)
        if not question or question.q_type != "multiple_choice":
            await callback.answer("Вопрос недоступен", show_alert=True)
            return
        options = question.options_list()
        if opt_idx < 0 or opt_idx >= len(options):
            await callback.answer("Неверный вариант", show_alert=True)
            return
        chosen = options[opt_idx]
        q_text = question.text
        await save_answer(
            session,
            question.id,
            callback.from_user.id,
            chosen,
            question_type="multiple_choice",
            selected_option=chosen,
            psychological_construct=question.psychological_construct or None,
            skipped=False,
        )
        quiz_id = question.quiz_id
        qs = (
            await session.execute(
                select(Question).where(Question.quiz_id == quiz_id).order_by(Question.position)
            )
        ).scalars().all()
        idx = next((i for i, q in enumerate(qs) if q.id == question.id), 0)
        total = len(qs)

    try:
        await callback.message.edit_text(
            f"{_progress(idx, total)}\n\n{q_text}\n\n✅ Ваш выбор: {chosen}",
            reply_markup=None,
        )
    except Exception:
        pass
    await callback.answer("Принято")
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    await _advance_after_answer(bot, callback.from_user.id, chat_id, state, quiz_id)


@router.callback_query(F.data.startswith("s:"))
async def on_skip(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not _only_partners_user(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        qid = int((callback.data or "").split(":")[1])
    except (IndexError, ValueError):
        await callback.answer()
        return

    q_text = ""
    async with get_session() as session:
        question = await session.get(Question, qid)
        if not question:
            await callback.answer("Вопрос недоступен", show_alert=True)
            return
        q_text = question.text
        await save_answer(
            session,
            question.id,
            callback.from_user.id,
            "[пропущено]",
            question_type=question.q_type,
            selected_option=None,
            psychological_construct=question.psychological_construct or None,
            skipped=True,
        )
        quiz_id = question.quiz_id
        qs = (
            await session.execute(
                select(Question).where(Question.quiz_id == quiz_id).order_by(Question.position)
            )
        ).scalars().all()
        idx = next((i for i, q in enumerate(qs) if q.id == question.id), 0)
        total = len(qs)

    try:
        await callback.message.edit_text(
            f"{_progress(idx, total)}\n\n{q_text}\n\n⏭ Вопрос пропущен",
            reply_markup=None,
        )
    except Exception:
        pass
    await callback.answer("Пропущено")
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    await _advance_after_answer(bot, callback.from_user.id, chat_id, state, quiz_id)


@router.callback_query(F.data.startswith("d:"))
async def on_discuss(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not _only_partners_user(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        quiz_id = int((callback.data or "").split(":")[1])
    except (IndexError, ValueError):
        await callback.answer()
        return
    await callback.answer()
    uid = callback.from_user.id

    async def _reply(text: str) -> None:
        if callback.message:
            await callback.message.answer(
                text, reply_markup=ob.main_menu_keyboard(uid)
            )

    await start_discussion_for_user(
        user_id=int(uid), quiz_id=quiz_id, state=state, reply=_reply
    )


async def process_discussion_turn(
    *,
    quiz_id: int,
    user_id: int,
    user_text: str,
    name: str = "",
    topic: str = "",
    analysis: str = "",
    quiz_context: str = "",
) -> tuple[str, str | None]:
    """
    Общая логика хода обсуждения (бот + API).
    Returns: (reply_text, error_code|None)
    error_code: rate_limit | daily_limit | empty
    """
    text = (user_text or "").strip()
    if len(text) < 1:
        return ("Напиши пару слов — я рядом.", "empty")
    if len(text) > 2000:
        text = text[:2000]

    async with get_session() as session:
        last_at = await get_last_user_discussion_at(session, quiz_id, user_id)
        if last_at and (datetime.utcnow() - last_at).total_seconds() < 5:
            return ("Подожди пару секунд — я ещё думаю над прошлым сообщением.", "rate_limit")

        day_count = await count_user_discussion_messages_24h(session, quiz_id, user_id)
        if day_count >= 20:
            limit_msg = ai_service.DAILY_DISCUSSION_LIMIT_MSG
            await save_discussion_message(
                session, quiz_id=quiz_id, user_id=user_id, role="user", text=text
            )
            await save_discussion_message(
                session, quiz_id=quiz_id, user_id=user_id, role="lum", text=limit_msg
            )
            return (ai_service.as_lumen(limit_msg), "daily_limit")

        hist_rows = await get_discussion_history(session, quiz_id, user_id, limit=15)
        history = [{"role": r.role, "text": r.text or ""} for r in hist_rows]
        quiz = await session.get(Quiz, quiz_id)
        topic_s = topic or (
            config.mood_label(quiz.topic_code)
            if quiz and quiz.topic_code in config.MOOD_CATALOG
            else (quiz.topic if quiz else "")
        )
        analysis_s = analysis or ((quiz.analysis_text or "") if quiz else "") or quiz_context
        name_s = name or config.partner_name(user_id)

        await save_discussion_message(
            session, quiz_id=quiz_id, user_id=user_id, role="user", text=text
        )

    reply = await ai_service.discuss_with_psychologist(
        text,
        quiz_context,
        name=name_s,
        topic=topic_s,
        analysis=analysis_s,
        history=history,
    )
    async with get_session() as session:
        await save_discussion_message(
            session, quiz_id=quiz_id, user_id=user_id, role="lum", text=reply
        )
    return (reply, None)


@router.message(F.text, QuizFSM.discussing)
async def on_discuss_message(message: Message, state: FSMContext) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    raw = (message.text or "").strip()
    if raw.startswith("/"):
        return  # команды обработают другие хендлеры
    data = await state.get_data()
    quiz_id = data.get("quiz_id")
    if not quiz_id:
        await state.clear()
        await message.answer(
            ai_service.as_lumen("Сессия обсуждения сбилась. Открой квиз из Истории."),
            reply_markup=ob.main_menu_keyboard(message.from_user.id),
        )
        return

    reply, err = await process_discussion_turn(
        quiz_id=int(quiz_id),
        user_id=int(message.from_user.id),
        user_text=raw,
        name=config.partner_name(message.from_user.id),
        topic=data.get("discuss_topic") or "",
        analysis=data.get("discuss_analysis") or "",
        quiz_context=data.get("quiz_context") or "",
    )
    await message.answer(
        reply[:4000],
        reply_markup=ob.main_menu_keyboard(message.from_user.id),
    )


@router.message(F.text, QuizFSM.answering)
async def on_open_answer(message: Message, state: FSMContext, bot: Bot) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    if not message.text:
        return
    if message.text in ob.MENU_BUTTON_TEXTS:
        return
    data = await state.get_data()
    quiz_id = data.get("quiz_id")
    q_ids: list[int] = data.get("q_ids") or []
    index = int(data.get("index", 0))
    if quiz_id is None or not q_ids or index >= len(q_ids):
        # Сессия сбилась — проверим активный квиз
        async with get_session() as session:
            active = await get_collecting_quiz(session)
        if not active:
            await state.clear()
            await message.answer(
                ai_service.as_lumen(
                    "🌿 Кажется, ты не в квизе. Нажми /quiz или «Квиз», чтобы начать."
                ),
                reply_markup=ob.main_menu_keyboard(message.from_user.id),
            )
            return
        await state.clear()
        await message.answer(
            ai_service.as_lumen(
                "🌿 Сессия квиза сбилась. Нажми /quiz или дождись следующего вопроса."
            ),
            reply_markup=ob.main_menu_keyboard(message.from_user.id),
        )
        return

    async with get_session() as session:
        active = await get_collecting_quiz(session)
        question = await session.get(Question, q_ids[index])
        if not active or not question:
            await state.clear()
            await message.answer(
                ai_service.as_lumen(
                    "🌿 Кажется, ты не в квизе. Нажми /quiz или «Квиз», чтобы начать."
                ),
                reply_markup=ob.main_menu_keyboard(message.from_user.id),
            )
            return
        if question.q_type != "open_ended":
            total = len(q_ids)
            # detach for send outside session
            q_copy = question
            await _resend_current_quiz_question(
                message, question=q_copy, index=index, total=total
            )
            return
        await save_answer(
            session,
            question.id,
            message.from_user.id,
            message.text,
            question_type="open_ended",
            psychological_construct=question.psychological_construct or None,
            skipped=False,
        )
    await message.answer("Принято ✅")
    await _advance_after_answer(bot, message.from_user.id, message.chat.id, state, int(quiz_id))


@router.message(ob.DigestStates.waiting_for_reply, F.text)
async def handle_digest_reply(message: Message, state: FSMContext) -> None:
    """Пользователь ответил, что отозвалось в итогах недели.

    Зарегистрирован ДО catch_open_without_fsm, чтобы FSM digest не терялся
    даже если кто-то снимет StateFilter(None) с catch.
    """
    try:
        if not _only_partners_user(message.from_user.id if message.from_user else None):
            return
        text = (message.text or "").strip()
        if not text:
            return
        # Кнопки меню — выходим из диалога, не шлём в AI
        if text in ob.MENU_BUTTON_TEXTS:
            await state.clear()
            return

        data = await state.get_data()
        digest_id = data.get("digest_id")
        try:
            digest_id_int = int(digest_id) if digest_id is not None else None
        except (TypeError, ValueError):
            digest_id_int = None

        async with get_session() as session:
            row = await save_digest_reply(
                session,
                telegram_id=message.from_user.id,
                user_text=text,
                ai_reply="",
                digest_id=digest_id_int,
            )
            reply_id = row.id

        await message.answer(ai_service.as_lumen("Слышу тебя, минутку…"))
        try:
            ai_reply = await ai_service.respond_to_digest_reflection(text)
        except Exception as e:
            logging.error("Ошибка при ответе на дайджест: %s", e)
            await message.answer(
                "🌿 Люм задумался... Давай попробуем еще раз чуть позже."
            )
            return

        async with get_session() as session:
            await update_digest_reply_ai(session, reply_id, ai_reply)

        await state.clear()
        await message.answer(ai_reply[:4000], reply_markup=ob.main_menu_keyboard(message.from_user.id))
    except Exception as e:
        logging.error("Ошибка при ответе на дайджест: %s", e)
        try:
            await message.answer(
                "🌿 Люм задумался... Давай попробуем еще раз чуть позже."
            )
        except Exception:
            pass


@router.message(StateFilter(None), F.text)
async def catch_open_without_fsm(message: Message, state: FSMContext, bot: Bot) -> None:
    """Только без FSM (state is None).

    Должен быть ПОСЛЕ всех FSM text-хендлеров:
    OnboardingStates.*, QuizFSM.discussing, QuizFSM.answering, DigestStates.*.
    StateFilter(None) дополнительно страхует discuss/digest от перехвата.
    """
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    if not message.text or message.text in ob.MENU_BUTTON_TEXTS:
        return
    async with get_session() as session:
        quiz = await get_active_quiz(session)
        if not quiz or quiz.status != "collecting":
            return
        user = await get_user_by_telegram(session, message.from_user.id)
        if not user:
            return
        qs = (
            await session.execute(
                select(Question).where(Question.quiz_id == quiz.id).order_by(Question.position)
            )
        ).scalars().all()
        next_q = None
        next_idx = 0
        for i, q in enumerate(qs):
            a = await session.scalar(
                select(Answer).where(Answer.question_id == q.id, Answer.user_id == user.id)
            )
            if a is None:
                next_q = q
                next_idx = i
                break
        if next_q is None:
            return
        if next_q.q_type != "open_ended":
            await _resend_current_quiz_question(
                message, question=next_q, index=next_idx, total=len(qs)
            )
            return
        await save_answer(
            session,
            next_q.id,
            message.from_user.id,
            message.text,
            question_type="open_ended",
            psychological_construct=next_q.psychological_construct or None,
        )
        quiz_id = quiz.id
    await message.answer("Принято ✅")
    await _advance_after_answer(bot, message.from_user.id, message.chat.id, state, quiz_id)


def _build_comparison(payload: dict) -> str:
    lines = [f"🔓 Сравнение — тема: {payload.get('topic', '—')}\n"]
    for i, q in enumerate(payload.get("questions") or [], start=1):
        construct = q.get("psychological_construct") or ""
        tag = f" ({construct})" if construct else ""
        lines.append(f"{i}. {q['text']}{tag}")
        ans = q.get("answers") or []
        qtype = q.get("type") or "open_ended"

        if any(a.get("skipped") for a in ans):
            for a in ans:
                if a.get("skipped"):
                    lines.append(f"   ⏭ {a.get('name')}: пропущено")
                else:
                    val = a.get("selected_option") or a.get("text")
                    lines.append(f"   • {a.get('name')}: {val}")
        elif qtype == "multiple_choice" and len(ans) >= 2:
            a0 = ans[0].get("selected_option") or ans[0].get("text")
            a1 = ans[1].get("selected_option") or ans[1].get("text")
            n0, n1 = ans[0].get("name", "A"), ans[1].get("name", "B")
            if a0 == a1:
                lines.append(f"   💚 Вы оба выбрали: «{a0}»!")
            else:
                lines.append(f"   💛 Вы разошлись: {n0} — «{a0}», {n1} — «{a1}»")
        elif qtype == "multiple_choice" and len(ans) == 1:
            a0 = ans[0].get("selected_option") or ans[0].get("text")
            lines.append(f"   ✅ {ans[0].get('name')}: «{a0}»")
        else:
            for a in ans:
                lines.append(f"   • {a.get('name')}: {a.get('text')}")
        lines.append("")
    return "\n".join(lines).strip()


async def finish_quiz(bot: Bot, quiz_id: int) -> None:
    async with get_session() as session:
        quiz = await session.get(Quiz, quiz_id)
        if not quiz:
            return
        quiz.status = "exchanging"
        await session.commit()
        payload = await load_quiz_for_analysis(session, quiz_id)

    comparison = _build_comparison(payload)
    topic_code = ""
    async with get_session() as session:
        qrow = await session.get(Quiz, quiz_id)
        topic_code = (qrow.topic_code or "") if qrow else ""
    discuss_kb = _insights_keyboard(quiz_id, topic_code)
    for tg_id in config.partner_ids():
        try:
            await bot.send_message(
                tg_id,
                ai_service.as_lumen(ai_service.with_open_question(comparison[:3700])),
                reply_markup=discuss_kb,
            )
        except Exception as exc:
            logger.warning("comparison %s: %s", tg_id, exc)

    # Ежедневная аналитика Люма (обязательно после ответов обоих / соло)
    metrics: dict = {}
    try:
        analysis, metrics, analysis_ok = await analytics.analyze_daily_quiz(quiz_id)
    except Exception as exc:
        logger.exception("analyze_daily_quiz crashed: %s", exc)
        analysis = ai_service.as_lumen(
            "🌿 Люм немного задумался... Инсайт по квизу попробуем чуть позже."
        )
        analysis_ok = False
        metrics = {}

    async with get_session() as session:
        quiz = await session.get(Quiz, quiz_id)
        if quiz:
            if analysis_ok:
                quiz.status = "analyzed"
                quiz.analysis_text = analysis
                quiz.analyzed_at = datetime.utcnow()
                if metrics.get("temperature_score") is not None:
                    quiz.temperature_score = int(metrics["temperature_score"])
                if metrics.get("match_count") is not None:
                    quiz.match_count = int(metrics["match_count"])
                if metrics.get("question_count") is not None:
                    quiz.question_count = int(metrics["question_count"])
            else:
                # Незавершённый анализ — можно повторить позже
                quiz.status = "analysis_pending"
                quiz.analysis_text = None
                quiz.analyzed_at = None
            await session.commit()

    # Пуш «разбор готов» уходит отдельным scheduler-job (analysis_notify),
    # чтобы не дублировать сообщения и дать время на Mini App.
    if analysis_ok:
        logger.info(
            "analysis stored quiz_id=%s; notify deferred to analysis_notify job",
            quiz_id,
        )


async def send_weekly_digest(
    bot: Bot,
    only_chat: int | None = None,
    state: FSMContext | None = None,
) -> None:
    partners = list(config.partner_ids())
    uid1 = partners[0] if partners else 0
    uid2 = partners[1] if len(partners) > 1 else 0
    text, digest_id = await analytics.generate_weekly_digest(uid1, uid2)
    targets = [only_chat] if only_chat else partners
    skip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data=ob.CB_DIGEST_SKIP)]
        ]
    )
    followup = ai_service.as_lumen(ob.DIGEST_FOLLOWUP_QUESTION)
    for tg_id in targets:
        if not tg_id:
            continue
        try:
            await bot.send_message(tg_id, text[:3900])
            await bot.send_message(tg_id, followup, reply_markup=skip_kb)
            # Надёжный путь: FSMContext из апдейта (кнопка «Итоги недели»)
            if state is not None and only_chat and tg_id == only_chat:
                await state.set_state(ob.DigestStates.waiting_for_reply)
                await state.update_data(digest_id=digest_id)
                logger.info(
                    "DigestStates via message.state user=%s digest_id=%s",
                    tg_id,
                    digest_id,
                )
            else:
                await _set_digest_waiting(bot, tg_id, digest_id)
        except Exception as exc:
            logger.exception("digest send failed for %s: %s", tg_id, exc)


@router.callback_query(F.data == ob.CB_DIGEST_SKIP, ob.DigestStates.waiting_for_reply)
@router.callback_query(F.data == ob.CB_DIGEST_SKIP)
async def on_digest_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """Пропуск диалога после дайджеста."""
    try:
        if not callback.from_user or not _only_partners_user(callback.from_user.id):
            await callback.answer()
            return
        await state.clear()
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                ai_service.as_lumen("Хорошо, я рядом, когда захочешь поговорить"),
                reply_markup=ob.main_menu_keyboard(callback.from_user.id),
            )
    except Exception as e:
        logging.error("Ошибка skip дайджеста: %s", e)
        await callback.answer()


def setup_dispatcher(dp: Dispatcher) -> None:
    # Gating: анкета обязательна до квизов/аналитики (см. MANIFESTO.md)
    global _fsm_storage
    from middleware import OnboardingGateMiddleware

    _fsm_storage = dp.storage
    dp.message.middleware(OnboardingGateMiddleware())
    dp.callback_query.middleware(OnboardingGateMiddleware())
    dp.include_router(router)
