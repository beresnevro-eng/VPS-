"""
Ежедневный квиз (адаптивное время) + пуши + воскресный дайджест (APScheduler).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

import ai_service
import config
import onboarding as ob
from database import (
    Quiz,
    UserProfile,
    WeeklyDigest,
    _is_same_utc_day,
    couple_blocked_topics,
    delete_proposed_date_ideas,
    get_digest_for_week,
    get_latest_analyzed_quiz,
    get_or_create_profile,
    get_quiz_created_today,
    get_session,
    get_user_by_telegram,
    list_done_idea_titles,
    list_note_texts_for_user,
    list_quizzes_pending_analysis_notify,
    load_quiz_for_analysis,
    save_date_ideas,
    save_weekly_digest,
    week_bounds_utc,
)
from handlers import (
    _pick_auto_topic_code,
    deliver_existing_quiz_to_user,
    start_quiz_for_pair,
)

logger = logging.getLogger(__name__)

DEFAULT_QUIZ_HOUR = 10  # UTC fallback


async def _build_week_quiz_payloads(
    session, week_start: datetime, week_end: datetime
) -> tuple[list[dict], list[Quiz]]:
    quizzes = (
        await session.execute(
            select(Quiz)
            .where(
                Quiz.status == "analyzed",
                Quiz.created_at >= week_start,
                Quiz.created_at <= week_end,
            )
            .order_by(Quiz.created_at.asc())
        )
    ).scalars().all()

    partners = list(config.partner_ids())
    name_a = config.partner_name(partners[0]) if partners else "A"
    name_b = config.partner_name(partners[1]) if len(partners) > 1 else "B"

    payloads: list[dict] = []
    for quiz in quizzes:
        try:
            raw = await load_quiz_for_analysis(session, quiz.id)
        except Exception:
            logger.exception("digest load quiz %s", quiz.id)
            continue
        answers_a: list[str] = []
        answers_b: list[str] = []
        for q in raw.get("questions") or []:
            for ans in q.get("answers") or []:
                label = ans.get("selected_option") or ans.get("text") or ""
                if ans.get("skipped"):
                    label = "(пропущен)"
                if ans.get("name") == name_a:
                    answers_a.append(str(label))
                elif ans.get("name") == name_b:
                    answers_b.append(str(label))
                else:
                    if len(answers_a) <= len(answers_b):
                        answers_a.append(str(label))
                    else:
                        answers_b.append(str(label))
        dt = quiz.created_at or datetime.utcnow()
        payloads.append(
            {
                "date": dt.strftime("%Y-%m-%d"),
                "topic": quiz.topic or raw.get("topic") or "",
                "questions": [q.get("text") for q in (raw.get("questions") or [])],
                "answers_a": answers_a,
                "answers_b": answers_b,
                "analysis": quiz.analysis_text or "",
                "temperature_score": quiz.temperature_score,
            }
        )
    return payloads, list(quizzes)


async def _build_profiles_payload(session) -> dict:
    partners = list(config.partner_ids())
    out: dict = {}
    keys = ("A", "B")
    for key, tg in zip(keys, partners):
        user = await get_user_by_telegram(session, tg)
        profile = await session.get(UserProfile, tg)
        out[key] = {
            "name": (user.name if user else "") or config.partner_name(tg),
            "ai_summary_public": (
                (profile.ai_summary_public or profile.ai_summary or "")[:800]
                if profile
                else ""
            ),
        }
    return out


async def generate_and_send_weekly_digest(bot: Bot) -> None:
    """
    Воскресный пайплайн: AI-дайджест → SQLite → пуш обоим партнёрам.
    Триггер: воскресенье 20:00 UTC.
    """
    import json

    week_start, week_end = week_bounds_utc()
    partners_ids = list(config.partner_ids())
    uid1 = partners_ids[0] if partners_ids else 0
    uid2 = partners_ids[1] if len(partners_ids) > 1 else 0

    async with get_session() as session:
        existing = await get_digest_for_week(session, week_start)
        if existing and existing.sent_at:
            logger.info("Дайджест недели уже отправлен id=%s", existing.id)
            return
        payloads, quiz_rows = await _build_week_quiz_payloads(session, week_start, week_end)
        profiles = await _build_profiles_payload(session)
        notes_a = (
            await list_note_texts_for_user(session, uid1, days=7) if uid1 else []
        )
        notes_b = (
            await list_note_texts_for_user(session, uid2, days=7) if uid2 else []
        )

    ai_result = await ai_service.generate_weekly_digest(
        payloads, profiles, notes_a=notes_a, notes_b=notes_b
    )
    if not ai_result:
        logger.warning("AI дайджест пустой — пропускаем неделю")
        return

    pattern = str(ai_result.get("pattern") or "").strip()
    pattern_short = str(ai_result.get("pattern_short") or "").strip()[:200]
    actions = [str(a).strip() for a in (ai_result.get("actions") or []) if str(a).strip()][:3]
    while len(actions) < 3:
        actions.append("Выделите 15 минут друг для друга без телефонов.")

    temps = [q.temperature_score for q in quiz_rows if q.temperature_score is not None]
    avg_temp = (sum(temps) / len(temps)) if temps else None
    actions_lines = "\n".join(f"• {a}" for a in actions)
    formatted = ai_service.as_lumen(
        f"🌿 Итоги вашей недели\n\n{pattern}\n\n{actions_lines}"
    )
    raw_json = json.dumps(ai_result, ensure_ascii=False)

    async with get_session() as session:
        digest = await save_weekly_digest(
            session,
            user_id_1=uid1,
            user_id_2=uid2 or 0,
            pattern=pattern,
            actions=actions,
            raw_json=raw_json,
            formatted_text=formatted,
            week_start=week_start,
            week_end=week_end,
            pattern_short=pattern_short,
            quiz_count=len(quiz_rows),
            avg_temperature=avg_temp,
            sent_at=datetime.utcnow(),
        )
        digest_id = digest.id

        blocked = await couple_blocked_topics(session)
        done_titles = await list_done_idea_titles(session, days=90)
        idea_notes_a = (
            await list_note_texts_for_user(session, uid1, days=30) if uid1 else []
        )
        idea_notes_b = (
            await list_note_texts_for_user(session, uid2, days=30) if uid2 else []
        )
        ideas_raw = await ai_service.generate_date_ideas(
            profiles,
            {"pattern": pattern, "pattern_short": pattern_short},
            blocked,
            done_ideas_titles=done_titles,
            notes_a=idea_notes_a,
            notes_b=idea_notes_b,
        )
        if ideas_raw:
            await delete_proposed_date_ideas(session)
            await save_date_ideas(session, ideas_raw, digest_id=digest_id)

    for tg_id in partners_ids:
        try:
            await bot.send_message(tg_id, formatted[:3900])
            logger.info("push type=weekly_digest user_id=%s", tg_id)
        except Exception as exc:
            logger.warning("digest push %s: %s", tg_id, exc)


def _effective_hour(profile: UserProfile | None) -> int:
    if profile and profile.preferred_hour is not None:
        try:
            return max(0, min(23, int(profile.preferred_hour)))
        except (TypeError, ValueError):
            pass
    return DEFAULT_QUIZ_HOUR


def _night_quiet(hour: int, *, preferred: int | None = None) -> bool:
    """00–06 UTC — тишина, кроме случаев, когда preferred сам ночной."""
    if not (0 <= hour < 7):
        return False
    if preferred is not None and 0 <= int(preferred) < 7:
        return False
    return True


async def adaptive_quiz_hourly_job(bot: Bot) -> None:
    """
    Каждый час (:05 UTC): шлёт квиз партнёрам в их preferred_hour.
    Вопросы генерируются один раз; второй партнёр получает тот же Quiz позже.
    """
    now = datetime.utcnow()
    hour = now.hour
    partners = list(config.partner_ids())
    if not partners:
        return

    async with get_session() as session:
        profiles: dict[int, UserProfile] = {}
        for tg in partners:
            profiles[tg] = await get_or_create_profile(session, tg)

        already_all = all(
            _is_same_utc_day(profiles[tg].last_quiz_sent_at, now) for tg in partners
        )
        if already_all:
            return

        today_quiz = await get_quiz_created_today(session)

        if hour == 22 and not today_quiz:
            any_sent = any(
                _is_same_utc_day(profiles[tg].last_quiz_sent_at, now) for tg in partners
            )
            if not any_sent:
                # Никто не попал в своё окно за день — завтра сработает default 10:00
                logger.info(
                    "adaptive quiz: nobody received today by 22:00 UTC; "
                    "fallback → default hour %s tomorrow",
                    DEFAULT_QUIZ_HOUR,
                )

        eligible: list[int] = []
        for tg in partners:
            pref = profiles[tg].preferred_hour
            effective = _effective_hour(profiles[tg])
            if _night_quiet(hour, preferred=pref if pref is not None else None):
                # ночь и effective не ночной — пропускаем
                if not (0 <= effective < 7):
                    continue
            if hour != effective:
                continue
            if _is_same_utc_day(profiles[tg].last_quiz_sent_at, now):
                continue
            eligible.append(tg)

    if not eligible:
        return

    logger.info(
        "adaptive quiz hour=%s eligible=%s today_quiz=%s",
        hour,
        eligible,
        today_quiz.id if today_quiz else None,
    )

    if today_quiz:
        for tg in eligible:
            try:
                await deliver_existing_quiz_to_user(bot, today_quiz, tg)
            except Exception:
                logger.exception("deliver existing quiz to %s", tg)
        return

    # Создаём новый квиз и шлём только eligible
    async with get_session() as session:
        blocked = await couple_blocked_topics(session)
    code = _pick_auto_topic_code(blocked)
    if not code:
        logger.warning("adaptive quiz: all topics blocked")
        return
    try:
        await start_quiz_for_pair(
            bot,
            code,
            automatic=True,
            close_active=True,
            send_to=eligible,
        )
    except Exception:
        logger.exception("adaptive quiz create failed")


async def reactivation_daily_job(bot: Bot) -> None:
    """19:00 UTC: мягкий пуш, если ≥3 дня без analyzed-квиза."""
    now = datetime.utcnow()
    if _night_quiet(now.hour):
        return

    async with get_session() as session:
        latest = await get_latest_analyzed_quiz(session)
        if not latest or not latest.analyzed_at:
            return
        days = (now - latest.analyzed_at).total_seconds() / 86400.0
        if days < 3:
            return
        days_i = max(3, int(days))

        for tg in config.partner_ids():
            profile = await get_or_create_profile(session, tg)
            enabled = (
                True
                if profile.reactivation_enabled is None
                else bool(profile.reactivation_enabled)
            )
            if not enabled:
                continue
            last = profile.last_reactivation_sent_at
            if last and (now - last).total_seconds() < 5 * 86400:
                continue
            text = ai_service.as_lumen(
                f"🌿 Привет. Мы не разговаривали {days_i} дней.\n"
                "Хочешь начать новый разговор? У меня есть тема, которая может быть "
                "интересна — просто нажми /quiz."
            )
            try:
                await bot.send_message(tg, text)
                profile.last_reactivation_sent_at = now
                profile.updated_at = now
                await session.commit()
                logger.info("push type=reactivation user_id=%s days=%s", tg, days_i)
            except Exception as exc:
                logger.warning("reactivation %s: %s", tg, exc)


def _analysis_preview(text: str, lines: int = 2) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    parts = [p.strip() for p in raw.splitlines() if p.strip()]
    if not parts:
        return raw[:180]
    return "\n".join(parts[:lines])[:400]


async def analysis_notify_hourly_job(bot: Bot) -> None:
    """Каждый час :30 — пуш «разбор готов» для свежих analyzed квизов."""
    now = datetime.utcnow()
    if _night_quiet(now.hour):
        return

    async with get_session() as session:
        quizzes = await list_quizzes_pending_analysis_notify(session, max_age_hours=6)
        if not quizzes:
            return

        for quiz in quizzes:
            topic = quiz.topic or "квиз"
            preview = _analysis_preview(quiz.analysis_text or "")
            body = (
                f"💡 Люм закончил разбор квиза «{topic}».\n"
                f"{preview}\n\n"
                "Полный разбор и 3 инсайта — в приложении."
            )
            text = ai_service.as_lumen(body)
            sent_any = False
            for tg in config.partner_ids():
                profile = await get_or_create_profile(session, tg)
                enabled = (
                    True
                    if profile.analysis_notify_enabled is None
                    else bool(profile.analysis_notify_enabled)
                )
                if not enabled:
                    continue
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="📊 Открыть Mini App",
                                web_app=ob.mini_app_web_info_for(tg),
                            )
                        ]
                    ]
                )
                try:
                    await bot.send_message(tg, text[:3900], reply_markup=kb)
                    sent_any = True
                    logger.info(
                        "push type=analysis_ready user_id=%s quiz_id=%s",
                        tg,
                        quiz.id,
                    )
                except Exception as exc:
                    logger.warning("analysis_notify %s quiz=%s: %s", tg, quiz.id, exc)

            # Помечаем даже если оба выключили — чтобы не крутить вечно
            row = await session.get(Quiz, quiz.id)
            if row and row.analysis_notified_at is None:
                row.analysis_notified_at = datetime.utcnow()
                await session.commit()
            if not sent_any:
                logger.info(
                    "analysis_notify quiz_id=%s skipped (all disabled)", quiz.id
                )


async def run_test_notifications(bot: Bot, chat_id: int) -> None:
    """Принудительно шлёт все три типа пушей в chat_id (отладка)."""
    # 1) quiz-style
    await bot.send_message(
        chat_id,
        ai_service.as_lumen(
            "🧪 Тест 1/3 — доставка квиза\n"
            "Так выглядит напоминание о ежедневном квизе."
        ),
    )
    logger.info("push type=test_quiz user_id=%s", chat_id)

    # 2) reactivation
    await bot.send_message(
        chat_id,
        ai_service.as_lumen(
            "🧪 Тест 2/3 — реактивация\n"
            "🌿 Привет. Мы не разговаривали 3 дня.\n"
            "Хочешь начать новый разговор? У меня есть тема, которая может быть "
            "интересна — просто нажми /quiz."
        ),
    )
    logger.info("push type=test_reactivation user_id=%s", chat_id)

    # 3) analysis ready
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Открыть Mini App",
                    web_app=ob.mini_app_web_info_for(chat_id),
                )
            ]
        ]
    )
    await bot.send_message(
        chat_id,
        ai_service.as_lumen(
            "🧪 Тест 3/3 — разбор готов\n"
            "💡 Люм закончил разбор квиза «Тестовая тема».\n"
            "Вы оба были внимательны к деталям.\n"
            "Есть тёплое совпадение в ожиданиях.\n\n"
            "Полный разбор и 3 инсайта — в приложении."
        ),
        reply_markup=kb,
    )
    logger.info("push type=test_analysis_ready user_id=%s", chat_id)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    # Jobs с временем UTC — явный timezone, не TIMEZONE сервера
    scheduler = AsyncIOScheduler(timezone="UTC")

    async def adaptive_job() -> None:
        try:
            await adaptive_quiz_hourly_job(bot)
        except Exception:
            logger.exception("Ошибка adaptive quiz job")

    async def reactivation_job() -> None:
        try:
            await reactivation_daily_job(bot)
        except Exception:
            logger.exception("Ошибка reactivation job")

    async def analysis_job() -> None:
        try:
            await analysis_notify_hourly_job(bot)
        except Exception:
            logger.exception("Ошибка analysis_notify job")

    async def digest_job() -> None:
        logger.info("Воскресный дайджест (UTC 20:00 pipeline)")
        try:
            await generate_and_send_weekly_digest(bot)
        except Exception:
            logger.exception("Ошибка дайджеста")

    scheduler.add_job(
        adaptive_job,
        CronTrigger(minute=5, timezone="UTC"),
        id="adaptive_daily_quiz",
        replace_existing=True,
    )
    scheduler.add_job(
        reactivation_job,
        CronTrigger(hour=19, minute=0, timezone="UTC"),
        id="reactivation_push",
        replace_existing=True,
    )
    scheduler.add_job(
        analysis_job,
        CronTrigger(minute=30, timezone="UTC"),
        id="analysis_ready_push",
        replace_existing=True,
    )
    scheduler.add_job(
        digest_job,
        CronTrigger(day_of_week="sun", hour=20, minute=0, timezone="UTC"),
        id="weekly_couple_digest",
        replace_existing=True,
    )
    logger.info(
        "Scheduler UTC: quiz hourly :05, reactivation 19:00, "
        "analysis :30, digest Sun 20:00"
    )
    return scheduler
