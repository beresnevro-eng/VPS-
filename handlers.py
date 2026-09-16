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
    get_or_create_profile,
    get_session,
    get_user_by_telegram,
    load_quiz_for_analysis,
    mark_onboarding_done_pending_summary,
    profile_answers_for_ai,
    profile_answers_split,
    profile_is_completed,
    save_answer,
    save_digest_reply,
    save_followup_answer,
    save_followup_questions,
    save_onboarding_answer,
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
        ob.format_base_question(step, q),
        reply_markup=ob.question_keyboard(step, q),
    )


async def start_onboarding(
    message: Message,
    state: FSMContext,
    resume: bool = True,
    telegram_id: int | None = None,
) -> None:
    """Старт или продолжение анкеты."""
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

    summary, ok = await ai_service.generate_profile_summary(base_ans, fu_ans)

    async with get_session() as session:
        if ok:
            await complete_profile(session, tg_id, summary)
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
    summary, ok = await ai_service.generate_profile_summary(base_ans, fu_ans)
    async with get_session() as session:
        if ok:
            await complete_profile(session, message.from_user.id, summary)
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


def _question_keyboard(question: Question) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if question.q_type == "multiple_choice":
        options = question.options_list()
        if _options_need_number_buttons(options):
            # Длинные варианты: кнопки 1 / 2 / 3 / 4 в один ряд
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
    header = f"{_progress(index, total)}{tag}\n\n{question.text}"

    if question.q_type == "multiple_choice":
        options = question.options_list()
        if _options_need_number_buttons(options):
            lines = [header, ""]
            for i, opt in enumerate(options, start=1):
                lines.append(f"{i}. {opt}")
            lines.append("")
            lines.append("Нажмите цифру 👇 или «Пропустить», если вопрос неудобен.")
            return "\n".join(lines)
        hint = "\n\nВыберите вариант кнопкой 👇\nИли нажмите «Пропустить», если вопрос неудобен."
        return f"{header}{hint}"

    hint = "\n\nНапишите ответ одним сообщением ✍️\nИли нажмите «Пропустить»."
    return f"{header}{hint}"


async def _send_question(bot: Bot, chat_id: int, question: Question, index: int, total: int) -> None:
    await bot.send_message(
        chat_id,
        _question_text(question, index, total),
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

    args = (command.args or "").strip()
    if args.startswith("discuss_"):
        await _handle_discuss_deeplink(message, state, args)
        return
    if args in ("onboarding", "start_onboarding"):
        await start_onboarding(message, state, resume=False)
        return

    await message.answer(
        ai_service.as_lumen(START_GREETING),
        reply_markup=ob.main_menu_keyboard(chat_id),
    )
    async with get_session() as session:
        done = await profile_is_completed(session, message.from_user.id)
    if not done:
        await start_onboarding(message, state, resume=True)
        return
    await state.clear()
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


async def _handle_discuss_deeplink(
    message: Message, state: FSMContext, args: str
) -> None:
    """Deep link: t.me/Bot?start=discuss_{quiz_id}"""
    raw = args.replace("discuss_", "", 1).strip()
    try:
        quiz_id = int(raw)
    except ValueError:
        await message.answer(
            ai_service.as_lumen("Не нашёл этот квиз. Открой историю в Mini App ещё раз."),
            reply_markup=ob.main_menu_keyboard(chat_id),
        )
        return

    async with get_session() as session:
        quiz = await session.get(Quiz, quiz_id)
        if not quiz:
            await message.answer(
                ai_service.as_lumen("Этот квиз уже не доступен."),
                reply_markup=ob.main_menu_keyboard(chat_id),
            )
            return
        analysis = (quiz.analysis_text or "").strip()
        topic = quiz.topic or ""
        topic_code = quiz.topic_code or ""
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

    await state.set_state(QuizFSM.discussing)
    await state.update_data(quiz_id=quiz_id, quiz_context="\n".join(ctx_lines)[:2000])

    label = config.mood_label(topic_code) if topic_code else (topic or f"Квиз #{quiz_id}")
    if analysis:
        await message.answer(
            ai_service.as_lumen(f"📖 {label}\n\n{analysis[:3500]}"),
            reply_markup=_insights_keyboard(quiz_id, topic_code),
        )
    else:
        await message.answer(
            ai_service.as_lumen(
                f"📖 {label}\n\nРазбор Люма ещё готовится или был коротким. "
                "Но мы всё равно можем обсудить ваши ответы."
            ),
            reply_markup=ob.main_menu_keyboard(chat_id),
        )

    await message.answer(
        ai_service.as_lumen(
            "Что тебя зацепило больше всего?\n"
            "Напиши пару слов — обсудим вместе.\n"
            "Выйти: /cancel"
        )
    )


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
        profile = await get_or_create_profile(session, message.from_user.id)
        profile.onboarding_step = 0
        profile.is_completed = False
        profile.raw_answers_json = "{}"
        profile.followup_json = "[]"
        profile.ai_summary = None
        profile.core_values = ""
        profile.love_language = ""
        profile.attachment_style = ""
        profile.conflict_style = ""
        profile.intimacy_views = ""
        await session.commit()
    await state.clear()
    await start_onboarding(message, state, resume=False)


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
            f"«{ob.BTN_DIGEST}» — итоги недели.\n\n"
            "Как пользоваться:\n"
            "• Утром бот сам присылает квиз — отвечайте кнопками в чате.\n"
            "• Вне очереди — откройте Шёпот → «Хочу обсудить сейчас».\n"
            "• Вечером — Шёпот → История / Мы.\n\n"
            "/onboarding — анкета заново\n"
            "/reset_topics — снова открыть закрытые темы\n"
            "/cancel — сбросить ввод\n\n"
            f"{ai_service.random_open_question()}"
        ),
        reply_markup=ob.main_menu_keyboard(message.from_user.id),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    await state.clear()
    await message.answer("Ок, сброшено.", reply_markup=ob.main_menu_keyboard(message.from_user.id))


@router.message(Command("status"))
@router.message(F.text == ob.BTN_STATUS)
async def cmd_status(message: Message) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    async with get_session() as session:
        quiz = await get_active_quiz(session)
        if not quiz:
            await message.answer("Активного квиза нет.", reply_markup=ob.main_menu_keyboard(message.from_user.id))
            return
        qs = (
            await session.execute(
                select(Question).where(Question.quiz_id == quiz.id).order_by(Question.position)
            )
        ).scalars().all()
        user = await get_user_by_telegram(session, message.from_user.id)
        done = 0
        if user:
            for q in qs:
                a = await session.scalar(
                    select(Answer).where(Answer.question_id == q.id, Answer.user_id == user.id)
                )
                if a:
                    done += 1
        await message.answer(
            f"Квиз #{quiz.id}\nТема: {quiz.topic}\nСтатус: {quiz.status}\n"
            f"Ваших ответов: {done}/{len(qs)}",
            reply_markup=ob.main_menu_keyboard(message.from_user.id),
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
            await message.answer(
                ai_service.as_lumen(
                    "У нас уже есть активный квиз на сегодня. "
                    "Давай сначала закончим его, а потом я задам новые вопросы."
                ),
                reply_markup=ob.main_menu_keyboard(message.from_user.id),
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
        await callback.message.edit_text(
            f"📋 Анкета {step + 1}/{ob.base_total()}\n\n{q['text']}\n\n✅ {chosen}",
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


async def start_daily_quiz(
    bot: Bot,
    topic: str | None = None,
    notify_chat: int | None = None,
    *,
    automatic: bool = False,
    topic_code: str | None = None,
) -> None:
    """
    automatic=True — плановый квиз в 10:00, без выбора настроения.
    automatic=False — ручной, тема уже выбрана.
    """
    await ensure_partners()
    async with get_session() as session:
        blocked = await couple_blocked_topics(session)
        if automatic:
            old = await get_active_quiz(session)
            if old and old.status == "collecting":
                old.status = "closed"
                await session.commit()

    resolved_code = topic_code
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
            return
        topic = config.mood_prompt(resolved_code)

    topic, questions = await ai_service.generate_questions(
        topic, blocked_topics=blocked
    )

    async with get_session() as session:
        quiz = await create_quiz_with_questions(
            session, topic, questions, topic_code=resolved_code or ""
        )
        q_rows = (
            await session.execute(
                select(Question).where(Question.quiz_id == quiz.id).order_by(Question.position)
            )
        ).scalars().all()
        quiz_id = quiz.id
        total = len(q_rows)
        first = q_rows[0] if q_rows else None
        code_for_btn = resolved_code or ""

    label = config.mood_label(code_for_btn) if code_for_btn else topic
    for tg_id in config.partner_ids():
        try:
            if automatic:
                intro = ai_service.as_lumen(
                    f"Доброе утро! Вот твой ежедневный квиз. "
                    f"Сегодня Люм подготовил вопросы на тему: {label}."
                )
            else:
                intro = ai_service.as_lumen(
                    f"💌 Квиз #{quiz_id}\nТема: {label}\nВопросов: {total}"
                )
            kb = _block_topic_keyboard(code_for_btn)
            await bot.send_message(
                tg_id,
                intro,
                reply_markup=kb if kb.inline_keyboard else None,
            )
            if first:
                await _send_question(bot, tg_id, first, 0, total)
        except Exception as exc:
            logger.warning("send %s: %s", tg_id, exc)

    if notify_chat and notify_chat not in config.partner_ids():
        await bot.send_message(notify_chat, f"Квиз #{quiz_id} разослан в личку.")


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
    """Пуш: один партнёр закончил — второму пора ответить."""
    name = config.partner_name(finished_user_id) or "Партнёр"
    text = ai_service.as_lumen(
        f"{name} ответил на квиз ❤️\n"
        "Открой чат и ответь — тогда Люм сравнит ваши ответы."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть бота",
                    url="https://t.me/Familia_Quiz_bot",
                )
            ]
        ]
    )
    for tg_id in config.partner_ids():
        if tg_id == finished_user_id:
            continue
        try:
            await bot.send_message(tg_id, text, reply_markup=kb)
        except Exception as exc:
            logger.warning("partner-wait notify %s (quiz %s): %s", tg_id, quiz_id, exc)


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

    async with get_session() as session:
        payload = await load_quiz_for_analysis(session, quiz_id)
    ctx_lines = [f"Тема: {payload.get('topic')}"]
    for i, q in enumerate(payload.get("questions", []), 1):
        ctx_lines.append(f"{i}. {q['text']}")
        for a in q.get("answers", []):
            mark = " (пропуск)" if a.get("skipped") else ""
            val = a.get("selected_option") or a.get("text")
            ctx_lines.append(f"   {a.get('name')}: {val}{mark}")

    await state.set_state(QuizFSM.discussing)
    await state.update_data(quiz_id=quiz_id, quiz_context="\n".join(ctx_lines)[:2000])
    await callback.answer()
    await callback.message.answer(
        ai_service.as_lumen(
            "Давайте обсудим это вместе.\n"
            "Напишите, что отозвалось или что хотите прояснить.\n"
            "Выйти: /cancel"
        )
    )


@router.message(F.text, QuizFSM.discussing)
async def on_discuss_message(message: Message, state: FSMContext) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    data = await state.get_data()
    ctx = data.get("quiz_context") or ""
    reply = await ai_service.discuss_with_psychologist(message.text or "", ctx)
    await message.answer(reply[:4000], reply_markup=ob.main_menu_keyboard(message.from_user.id))


@router.message(F.text, QuizFSM.answering)
async def on_open_answer(message: Message, state: FSMContext, bot: Bot) -> None:
    if not _only_partners_user(message.from_user.id if message.from_user else None):
        return
    if not message.text:
        return
    data = await state.get_data()
    quiz_id = data.get("quiz_id")
    q_ids: list[int] = data.get("q_ids") or []
    index = int(data.get("index", 0))
    if quiz_id is None or not q_ids or index >= len(q_ids):
        await state.clear()
        await message.answer("Сессия сбилась. /quiz")
        return

    async with get_session() as session:
        question = await session.get(Question, q_ids[index])
        if not question or question.q_type != "open_ended":
            await message.answer("Сейчас нужен выбор кнопкой (или «Пропустить»).")
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


@router.message(StateFilter(None), F.text)
async def catch_open_without_fsm(message: Message, state: FSMContext, bot: Bot) -> None:
    """Только без FSM — иначе перехватывает DigestStates / QuizFSM и молчит."""
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
        for q in qs:
            a = await session.scalar(
                select(Answer).where(Answer.question_id == q.id, Answer.user_id == user.id)
            )
            if a is None:
                next_q = q
                break
        if next_q is None:
            return
        if next_q.q_type != "open_ended":
            await message.answer("Ответьте кнопкой под вопросом 👆")
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
    try:
        analysis, analysis_ok = await analytics.analyze_daily_quiz(quiz_id)
    except Exception as exc:
        logger.exception("analyze_daily_quiz crashed: %s", exc)
        analysis = ai_service.as_lumen(
            "🌿 Люм немного задумался... Инсайт по квизу попробуем чуть позже."
        )
        analysis_ok = False

    async with get_session() as session:
        quiz = await session.get(Quiz, quiz_id)
        if quiz:
            if analysis_ok:
                quiz.status = "analyzed"
                quiz.analysis_text = analysis
                quiz.analyzed_at = datetime.utcnow()
            else:
                # Незавершённый анализ — можно повторить позже
                quiz.status = "analysis_pending"
                quiz.analysis_text = None
                quiz.analyzed_at = None
            await session.commit()

    for tg_id in config.partner_ids():
        try:
            kb = _block_topic_keyboard(topic_code)
            await bot.send_message(
                tg_id,
                analysis[:4000],
                reply_markup=kb if kb.inline_keyboard else None,
            )
        except Exception as exc:
            logger.warning("analysis %s: %s", tg_id, exc)


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


@router.message(ob.DigestStates.waiting_for_reply, F.text)
async def handle_digest_reply(message: Message, state: FSMContext) -> None:
    """Пользователь ответил, что отозвалось в итогах недели."""
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
