"""
Асинхронная работа с SQLite через SQLAlchemy 2.x.
Храним пользователей, квизы, вопросы и ответы.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import config


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    answers: Mapped[list["Answer"]] = relationship(back_populates="user")


class Quiz(Base):
    """Один ежедневный квиз (пакет из 5 вопросов)."""

    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(String(256))
    topic_code: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending | collecting | exchanging | analyzed | closed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    analyzed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    analysis_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    analysis_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Метрики динамики (nullable — старые квизы без дашборда)
    temperature_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    match_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    question_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    questions: Mapped[list["Question"]] = relationship(
        back_populates="quiz", cascade="all, delete-orphan"
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)  # 1..5
    text: Mapped[str] = mapped_column(Text)
    q_type: Mapped[str] = mapped_column(String(32), default="open_ended")
    # multiple_choice | open_ended
    options_json: Mapped[str] = mapped_column(Text, default="[]")
    psychological_construct: Mapped[str] = mapped_column(String(128), default="")

    quiz: Mapped["Quiz"] = relationship(back_populates="questions")
    answers: Mapped[list["Answer"]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )

    def options_list(self) -> list[str]:
        try:
            data = json.loads(self.options_json or "[]")
            return [str(x) for x in data] if isinstance(data, list) else []
        except Exception:
            return []


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (
        UniqueConstraint("question_id", "user_id", name="uq_answer_question_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(Text, default="")
    question_type: Mapped[str] = mapped_column(String(32), default="open_ended")
    selected_option: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    psychological_construct: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    revealed: Mapped[bool] = mapped_column(Boolean, default=False)

    question: Mapped["Question"] = relationship(back_populates="answers")
    user: Mapped["User"] = relationship(back_populates="answers")


class UserProfile(Base):
    """Портрет / онбординг (ключ = Telegram user id)."""

    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram ID
    onboarding_step: Mapped[int] = mapped_column(Integer, default=0)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    core_values: Mapped[str] = mapped_column(Text, default="")
    love_language: Mapped[str] = mapped_column(Text, default="")
    attachment_style: Mapped[str] = mapped_column(Text, default="")
    conflict_style: Mapped[str] = mapped_column(Text, default="")
    intimacy_views: Mapped[str] = mapped_column(Text, default="")
    # Род обращения: male | female | null (нейтрально / не указан)
    gender: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    # Все ответы анкеты (база + follow-up) как JSON-строка — не держим в RAM
    raw_answers_json: Mapped[str] = mapped_column(Text, default="{}")
    followup_json: Mapped[str] = mapped_column(Text, default="[]")  # 3 уточняющих вопроса
    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Публичная часть портрета (для партнёра в Mini App)
    ai_summary_public: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Приватная часть (зоны роста / уязвимости) — только себе
    ai_summary_private: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Коды заблокированных тем через запятую, напр. "routine,fun"
    blocked_topics: Mapped[str] = mapped_column(Text, default="")
    # Умные напоминания (UTC)
    preferred_hour: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    preferred_hour_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    last_quiz_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_reactivation_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    reactivation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    analysis_notify_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WeeklyDigest(Base):
    """Итоги недели (паттерн + 3 действия) — лёгкая запись в SQLite."""

    __tablename__ = "weekly_digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Границы недели (UTC)
    week_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    week_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    pattern: Mapped[str] = mapped_column(Text, default="")
    pattern_short: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # JSON: [{"text": "...", "done": false}, ...]
    actions_json: Mapped[str] = mapped_column(Text, default="[]")
    quiz_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_temperature: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # legacy / совместимость со старым путём analytics
    user_id_1: Mapped[int] = mapped_column(BigInteger, index=True, default=0)
    user_id_2: Mapped[int] = mapped_column(BigInteger, index=True, default=0)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    formatted_text: Mapped[str] = mapped_column(Text, default="")


class DigestReply(Base):
    """Ответ пользователя на вопрос после дайджеста + отклик Люма."""

    __tablename__ = "digest_replies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    digest_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("weekly_digests.id", ondelete="SET NULL"), nullable=True
    )
    user_text: Mapped[str] = mapped_column(Text, default="")
    ai_reply: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DateIdea(Base):
    """Персональные идеи для свиданий от Люма."""

    __tablename__ = "date_ideas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    duration_hint: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    budget_hint: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    # proposed | saved | done | dismissed
    digest_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("weekly_digests.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserNote(Base):
    """Приватная заметка пользователя (видит только автор)."""

    __tablename__ = "user_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    quiz_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("quizzes.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class HiddenQuestion(Base):
    """Приватный запрос темы в квиз (партнёр не видит автора)."""

    __tablename__ = "hidden_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    used_in_quiz_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("quizzes.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class DiscussionMessage(Base):
    """Приватный диалог обсуждения квиза (только автор user_id)."""

    __tablename__ = "discussion_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quiz_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("quizzes.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | lum | meta
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# --- движок и сессии ---

_engine = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def _db_url() -> str:
    path = config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{path}"


async def init_db() -> None:
    """Создаёт таблицы и фабрику сессий. Вызывать при старте бота."""
    global _engine, _session_factory
    _engine = create_async_engine(
        _db_url(),
        echo=False,
        pool_pre_ping=True,
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    def _run_migrate(sync_conn) -> None:
        def columns(table: str) -> set[str]:
            rows = sync_conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            return {r[1] for r in rows}

        tables = {r[0] for r in sync_conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )).fetchall()}
        if "questions" in tables:
            q_cols = columns("questions")
            for col, ddl in (
                ("q_type", "ALTER TABLE questions ADD COLUMN q_type VARCHAR(32) DEFAULT 'open_ended'"),
                ("options_json", "ALTER TABLE questions ADD COLUMN options_json TEXT DEFAULT '[]'"),
                (
                    "psychological_construct",
                    "ALTER TABLE questions ADD COLUMN psychological_construct VARCHAR(128) DEFAULT ''",
                ),
            ):
                if col not in q_cols:
                    sync_conn.execute(text(ddl))
        if "answers" in tables:
            a_cols = columns("answers")
            for col, ddl in (
                (
                    "question_type",
                    "ALTER TABLE answers ADD COLUMN question_type VARCHAR(32) DEFAULT 'open_ended'",
                ),
                ("selected_option", "ALTER TABLE answers ADD COLUMN selected_option VARCHAR(512)"),
                (
                    "psychological_construct",
                    "ALTER TABLE answers ADD COLUMN psychological_construct VARCHAR(128)",
                ),
                ("skipped", "ALTER TABLE answers ADD COLUMN skipped BOOLEAN DEFAULT 0"),
            ):
                if col not in a_cols:
                    sync_conn.execute(text(ddl))
        if "user_profiles" in tables:
            p_cols = columns("user_profiles")
            if "blocked_topics" not in p_cols:
                sync_conn.execute(
                    text("ALTER TABLE user_profiles ADD COLUMN blocked_topics TEXT DEFAULT ''")
                )
            if "ai_summary_public" not in p_cols:
                sync_conn.execute(
                    text("ALTER TABLE user_profiles ADD COLUMN ai_summary_public TEXT")
                )
            if "ai_summary_private" not in p_cols:
                sync_conn.execute(
                    text("ALTER TABLE user_profiles ADD COLUMN ai_summary_private TEXT")
                )
            if "gender" not in p_cols:
                sync_conn.execute(
                    text("ALTER TABLE user_profiles ADD COLUMN gender VARCHAR(10)")
                )
            for col, ddl in (
                ("preferred_hour", "ALTER TABLE user_profiles ADD COLUMN preferred_hour INTEGER"),
                (
                    "preferred_hour_updated_at",
                    "ALTER TABLE user_profiles ADD COLUMN preferred_hour_updated_at DATETIME",
                ),
                (
                    "last_quiz_sent_at",
                    "ALTER TABLE user_profiles ADD COLUMN last_quiz_sent_at DATETIME",
                ),
                (
                    "last_reactivation_sent_at",
                    "ALTER TABLE user_profiles ADD COLUMN last_reactivation_sent_at DATETIME",
                ),
                (
                    "reactivation_enabled",
                    "ALTER TABLE user_profiles ADD COLUMN reactivation_enabled BOOLEAN DEFAULT 1",
                ),
                (
                    "analysis_notify_enabled",
                    "ALTER TABLE user_profiles ADD COLUMN analysis_notify_enabled BOOLEAN DEFAULT 1",
                ),
            ):
                if col not in p_cols:
                    sync_conn.execute(text(ddl))
        if "weekly_digests" in tables:
            d_cols = columns("weekly_digests")
            for col, ddl in (
                ("week_start", "ALTER TABLE weekly_digests ADD COLUMN week_start DATETIME"),
                ("week_end", "ALTER TABLE weekly_digests ADD COLUMN week_end DATETIME"),
                ("pattern_short", "ALTER TABLE weekly_digests ADD COLUMN pattern_short VARCHAR(200)"),
                ("quiz_count", "ALTER TABLE weekly_digests ADD COLUMN quiz_count INTEGER DEFAULT 0"),
                ("avg_temperature", "ALTER TABLE weekly_digests ADD COLUMN avg_temperature FLOAT"),
                ("sent_at", "ALTER TABLE weekly_digests ADD COLUMN sent_at DATETIME"),
            ):
                if col not in d_cols:
                    sync_conn.execute(text(ddl))
        if "quizzes" in tables:
            z_cols = columns("quizzes")
            for col, ddl in (
                ("topic_code", "ALTER TABLE quizzes ADD COLUMN topic_code VARCHAR(32) DEFAULT ''"),
                ("temperature_score", "ALTER TABLE quizzes ADD COLUMN temperature_score INTEGER"),
                ("match_count", "ALTER TABLE quizzes ADD COLUMN match_count INTEGER"),
                ("question_count", "ALTER TABLE quizzes ADD COLUMN question_count INTEGER"),
                # analyzed_at уже в create_all; на старых БД могло отсутствовать
                ("analyzed_at", "ALTER TABLE quizzes ADD COLUMN analyzed_at DATETIME"),
                (
                    "analysis_notified_at",
                    "ALTER TABLE quizzes ADD COLUMN analysis_notified_at DATETIME",
                ),
            ):
                if col not in z_cols:
                    sync_conn.execute(text(ddl))
        # date_ideas / user_notes / hidden_questions / discussion_messages — create_all
        if "date_ideas" not in tables:
            pass
        if "user_notes" not in tables:
            pass
        if "hidden_questions" not in tables:
            pass
        if "discussion_messages" not in tables:
            pass
        if "hidden_questions" in tables:
            hq_cols = columns("hidden_questions")
            for col, ddl in (
                ("status", "ALTER TABLE hidden_questions ADD COLUMN status VARCHAR(20) DEFAULT 'pending'"),
                ("used_in_quiz_id", "ALTER TABLE hidden_questions ADD COLUMN used_in_quiz_id INTEGER"),
                ("used_at", "ALTER TABLE hidden_questions ADD COLUMN used_at DATETIME"),
            ):
                if col not in hq_cols:
                    sync_conn.execute(text(ddl))

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_run_migrate)


def get_session() -> AsyncSession:
    if _session_factory is None:
        raise RuntimeError("БД не инициализирована: вызовите await init_db()")
    return _session_factory()


async def ensure_partners() -> None:
    """Гарантирует наличие партнёров в таблице users (1 или 2)."""
    names = {
        config.PARTNER_A_ID: config.PARTNER_A_NAME,
        config.PARTNER_B_ID: config.PARTNER_B_NAME,
    }
    async with get_session() as session:
        for tg_id in config.partner_ids():
            name = names.get(tg_id) or config.partner_name(tg_id)
            row = await session.scalar(select(User).where(User.telegram_id == tg_id))
            if row is None:
                session.add(User(telegram_id=tg_id, name=name))
            else:
                row.name = name
        await session.commit()


async def get_user_by_telegram(session: AsyncSession, telegram_id: int) -> Optional[User]:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def create_quiz_with_questions(
    session: AsyncSession,
    topic: str,
    questions: list[dict[str, Any]],
    topic_code: str = "",
) -> Quiz:
    quiz = Quiz(topic=topic, topic_code=(topic_code or "")[:32], status="collecting")
    session.add(quiz)
    await session.flush()
    for i, item in enumerate(questions, start=1):
        opts = item.get("options") or []
        session.add(
            Question(
                quiz_id=quiz.id,
                position=i,
                text=str(item.get("question") or "").strip(),
                q_type=str(item.get("type") or "open_ended"),
                options_json=json.dumps(opts, ensure_ascii=False),
                psychological_construct=str(item.get("psychological_construct") or "")[:120],
            )
        )
    await session.commit()
    await session.refresh(quiz)
    return quiz


async def get_active_quiz(session: AsyncSession) -> Optional[Quiz]:
    return await session.scalar(
        select(Quiz)
        .where(Quiz.status.in_(("collecting", "exchanging")))
        .order_by(Quiz.id.desc())
        .limit(1)
    )


async def both_answered_quiz(session: AsyncSession, quiz_id: int) -> bool:
    """True, если все участники ответили на все вопросы квиза."""
    result = await session.execute(select(Question).where(Question.quiz_id == quiz_id))
    questions = list(result.scalars())
    if not questions:
        return False

    users = []
    for tg in config.partner_ids():
        u = await get_user_by_telegram(session, tg)
        if not u:
            return False
        users.append(u)

    for q in questions:
        for u in users:
            ans = await session.scalar(
                select(Answer).where(
                    Answer.question_id == q.id,
                    Answer.user_id == u.id,
                )
            )
            if ans is None:
                return False
    return True


async def user_finished_quiz(
    session: AsyncSession, quiz_id: int, telegram_id: int
) -> bool:
    """True, если пользователь ответил на все вопросы квиза."""
    user = await get_user_by_telegram(session, telegram_id)
    if not user:
        return False
    questions = (
        await session.execute(select(Question).where(Question.quiz_id == quiz_id))
    ).scalars().all()
    if not questions:
        return False
    for q in questions:
        ans = await session.scalar(
            select(Answer).where(
                Answer.question_id == q.id,
                Answer.user_id == user.id,
            )
        )
        if ans is None:
            return False
    return True


def _streak_as_utc(dt: datetime | None) -> datetime | None:
    """Нормализует datetime к naive UTC для сравнений."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


async def calculate_streak(
    session: AsyncSession, couple_partner_ids: list[int]
) -> dict[str, Any]:
    """
    Серия дней по завершённым (analyzed) квизам, где ответили оба партнёра.

    Между соседними квизами разрыв ≤ 36 часов — серия продолжается.
    Если последний квиз старше 36 часов — current_streak = 0.
    """
    empty = {
        "current_streak": 0,
        "longest_streak": 0,
        "last_quiz_at": None,
        "days_since_last": 0,
    }
    partner_ids = [int(x) for x in (couple_partner_ids or []) if x]
    if len(partner_ids) < 1:
        return empty

    quizzes = (
        await session.execute(
            select(Quiz)
            .where(Quiz.status == "analyzed")
            .order_by(Quiz.created_at.desc(), Quiz.id.desc())
        )
    ).scalars().all()

    completed: list[Quiz] = []
    for q in quizzes:
        ok = True
        for tg in partner_ids:
            if not await user_finished_quiz(session, q.id, tg):
                ok = False
                break
        if ok:
            completed.append(q)

    if not completed:
        return empty

    # Временные метки newest → oldest
    times: list[datetime] = []
    for q in completed:
        ts = _streak_as_utc(q.analyzed_at) or _streak_as_utc(q.created_at)
        if ts is None:
            continue
        times.append(ts)

    if not times:
        return empty

    now = datetime.utcnow()
    last_at = times[0]
    days_since_last = max(0, (now.date() - last_at.date()).days)

    gap_limit = timedelta(hours=36)

    def chain_length(start_idx: int) -> int:
        length = 1
        for i in range(start_idx, len(times) - 1):
            newer, older = times[i], times[i + 1]
            if newer - older <= gap_limit:
                length += 1
            else:
                break
        return length

    # longest: максимум по всем сегментам
    longest = 0
    i = 0
    while i < len(times):
        length = chain_length(i)
        if length > longest:
            longest = length
        i += length

    # current: только если последний квиз не старше 36ч
    if now - last_at > gap_limit:
        current = 0
    else:
        current = chain_length(0)

    return {
        "current_streak": current,
        "longest_streak": longest,
        "last_quiz_at": last_at,
        "days_since_last": days_since_last,
    }


async def get_collecting_quiz(session: AsyncSession) -> Optional[Quiz]:
    """Активный квиз в фазе сбора ответов."""
    return await session.scalar(
        select(Quiz)
        .where(Quiz.status == "collecting")
        .order_by(Quiz.id.desc())
        .limit(1)
    )


async def save_answer(
    session: AsyncSession,
    question_id: int,
    telegram_id: int,
    text: str,
    *,
    question_type: str = "open_ended",
    selected_option: str | None = None,
    psychological_construct: str | None = None,
    skipped: bool = False,
) -> Answer:
    user = await get_user_by_telegram(session, telegram_id)
    if user is None:
        raise ValueError(f"Пользователь {telegram_id} не найден")
    existing = await session.scalar(
        select(Answer).where(
            Answer.question_id == question_id,
            Answer.user_id == user.id,
        )
    )
    if existing:
        existing.text = text.strip()
        existing.question_type = question_type
        existing.selected_option = selected_option
        existing.psychological_construct = psychological_construct
        existing.skipped = skipped
        await session.commit()
        await session.refresh(existing)
        try:
            await update_preferred_hour(session, telegram_id)
        except Exception:
            logging.getLogger(__name__).exception(
                "update_preferred_hour failed for %s", telegram_id
            )
        return existing
    ans = Answer(
        question_id=question_id,
        user_id=user.id,
        text=text.strip(),
        question_type=question_type,
        selected_option=selected_option,
        psychological_construct=psychological_construct,
        skipped=skipped,
    )
    session.add(ans)
    await session.commit()
    await session.refresh(ans)
    try:
        await update_preferred_hour(session, telegram_id)
    except Exception:
        logging.getLogger(__name__).exception(
            "update_preferred_hour failed for %s", telegram_id
        )
    return ans


def _median_int(values: list[int]) -> int | None:
    if not values:
        return None
    arr = sorted(values)
    n = len(arr)
    mid = n // 2
    if n % 2:
        return int(arr[mid])
    # для часов берём нижнюю медиану (устойчивее)
    return int(arr[mid - 1])


async def update_preferred_hour(session: AsyncSession, user_id: int) -> None:
    """
    Медианный час ответов (UTC) по последним 10 Answer.
    user_id = Telegram ID. Обновляет preferred_hour, если сдвиг > 1ч.
    """
    user = await get_user_by_telegram(session, int(user_id))
    if not user:
        return
    rows = (
        await session.execute(
            select(Answer)
            .where(Answer.user_id == user.id)
            .order_by(Answer.created_at.desc())
            .limit(10)
        )
    ).scalars().all()
    hours = [int(a.created_at.hour) for a in rows if a.created_at]
    if len(hours) < 3:
        return
    median = _median_int(hours)
    if median is None:
        return
    median = max(0, min(23, median))
    profile = await get_or_create_profile(session, int(user_id))
    current = profile.preferred_hour
    if current is not None and abs(int(current) - median) <= 1:
        return
    profile.preferred_hour = median
    profile.preferred_hour_updated_at = datetime.utcnow()
    profile.updated_at = datetime.utcnow()
    await session.commit()


async def touch_last_quiz_sent(session: AsyncSession, telegram_id: int) -> None:
    profile = await get_or_create_profile(session, int(telegram_id))
    profile.last_quiz_sent_at = datetime.utcnow()
    profile.updated_at = datetime.utcnow()
    await session.commit()


def _is_same_utc_day(a: datetime | None, b: datetime | None = None) -> bool:
    if not a:
        return False
    ref = b or datetime.utcnow()
    return a.date() == ref.date()


async def get_quiz_created_today(session: AsyncSession) -> Optional[Quiz]:
    """Квиз, созданный сегодня (UTC), ещё актуальный для ответов/доставки."""
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return await session.scalar(
        select(Quiz)
        .where(
            Quiz.created_at >= start,
            Quiz.status.in_(
                ("collecting", "exchanging", "analyzed", "analysis_pending", "pending")
            ),
        )
        .order_by(Quiz.created_at.desc())
        .limit(1)
    )


async def list_quizzes_pending_analysis_notify(
    session: AsyncSession, *, max_age_hours: int = 6
) -> list[Quiz]:
    since = datetime.utcnow() - timedelta(hours=max(1, max_age_hours))
    rows = (
        await session.execute(
            select(Quiz)
            .where(
                Quiz.status == "analyzed",
                Quiz.analysis_notified_at.is_(None),
                Quiz.analyzed_at.is_not(None),
                Quiz.analyzed_at >= since,
            )
            .order_by(Quiz.analyzed_at.asc())
            .limit(20)
        )
    ).scalars().all()
    return list(rows)


async def get_latest_analyzed_quiz(session: AsyncSession) -> Optional[Quiz]:
    return await session.scalar(
        select(Quiz)
        .where(Quiz.status == "analyzed", Quiz.analyzed_at.is_not(None))
        .order_by(Quiz.analyzed_at.desc())
        .limit(1)
    )


async def update_notification_settings(
    session: AsyncSession,
    telegram_id: int,
    *,
    preferred_hour: int | None = None,
    reactivation_enabled: bool | None = None,
    analysis_notify_enabled: bool | None = None,
) -> UserProfile:
    profile = await get_or_create_profile(session, int(telegram_id))
    if preferred_hour is not None:
        h = int(preferred_hour)
        if h < 0 or h > 23:
            raise ValueError("preferred_hour must be 0..23")
        profile.preferred_hour = h
        profile.preferred_hour_updated_at = datetime.utcnow()
    if reactivation_enabled is not None:
        profile.reactivation_enabled = bool(reactivation_enabled)
    if analysis_notify_enabled is not None:
        profile.analysis_notify_enabled = bool(analysis_notify_enabled)
    profile.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(profile)
    return profile


def serialize_notification_settings(profile: UserProfile | None) -> dict[str, Any]:
    if not profile:
        return {
            "preferred_hour": None,
            "preferred_hour_updated_at": None,
            "reactivation_enabled": True,
            "analysis_notify_enabled": True,
            "last_quiz_sent_at": None,
            "last_reactivation_sent_at": None,
        }
    return {
        "preferred_hour": profile.preferred_hour,
        "preferred_hour_updated_at": (
            profile.preferred_hour_updated_at.isoformat() + "Z"
            if profile.preferred_hour_updated_at
            else None
        ),
        "reactivation_enabled": bool(
            True if profile.reactivation_enabled is None else profile.reactivation_enabled
        ),
        "analysis_notify_enabled": bool(
            True
            if profile.analysis_notify_enabled is None
            else profile.analysis_notify_enabled
        ),
        "last_quiz_sent_at": (
            profile.last_quiz_sent_at.isoformat() + "Z"
            if profile.last_quiz_sent_at
            else None
        ),
        "last_reactivation_sent_at": (
            profile.last_reactivation_sent_at.isoformat() + "Z"
            if profile.last_reactivation_sent_at
            else None
        ),
    }


async def load_quiz_for_analysis(session: AsyncSession, quiz_id: int) -> dict:
    """Структура для сравнения ответов и AI-инсайта."""
    quiz = await session.get(Quiz, quiz_id)
    if not quiz:
        raise ValueError("Квиз не найден")

    q_rows = (
        await session.execute(
            select(Question).where(Question.quiz_id == quiz_id).order_by(Question.position)
        )
    ).scalars().all()

    payload: dict[str, Any] = {"topic": quiz.topic, "questions": []}
    for q in q_rows:
        a_rows = (
            await session.execute(select(Answer).where(Answer.question_id == q.id))
        ).scalars().all()
        answers = []
        for a in a_rows:
            u = await session.get(User, a.user_id)
            answers.append(
                {
                    "name": u.name if u else "?",
                    "telegram_id": u.telegram_id if u else 0,
                    "text": a.text,
                    "selected_option": a.selected_option,
                    "question_type": a.question_type,
                    "skipped": bool(a.skipped),
                }
            )
        payload["questions"].append(
            {
                "text": q.text,
                "type": q.q_type,
                "options": q.options_list(),
                "psychological_construct": q.psychological_construct,
                "answers": answers,
            }
        )
    return payload


async def weekly_quiz_facts(session: AsyncSession, days: int = 7) -> str:
    """Краткие факты за неделю для дайджеста."""
    from datetime import timedelta

    since = datetime.utcnow() - timedelta(days=days)
    quizzes = (
        await session.execute(
            select(Quiz)
            .where(Quiz.created_at >= since, Quiz.status.in_(("analyzed", "closed", "exchanging", "analysis_pending")))
            .order_by(Quiz.id.desc())
        )
    ).scalars().all()
    if not quizzes:
        return "За неделю квизов почти не было."

    match_constructs: dict[str, int] = {}
    diverge_constructs: dict[str, int] = {}
    total_q = 0
    for quiz in quizzes:
        payload = await load_quiz_for_analysis(session, quiz.id)
        for q in payload["questions"]:
            total_q += 1
            construct = q.get("psychological_construct") or "Общее"
            ans = q.get("answers") or []
            if len(ans) < 2:
                continue
            if any(a.get("skipped") for a in ans):
                continue
            a0 = ans[0].get("selected_option") or ans[0].get("text")
            a1 = ans[1].get("selected_option") or ans[1].get("text")
            if q.get("type") == "multiple_choice":
                if a0 == a1:
                    match_constructs[construct] = match_constructs.get(construct, 0) + 1
                else:
                    diverge_constructs[construct] = diverge_constructs.get(construct, 0) + 1

    def top(d: dict[str, int]) -> str:
        if not d:
            return "—"
        k = max(d, key=d.get)
        return f"{k} ({d[k]})"

    return (
        f"Квизов за неделю: {len(quizzes)}, вопросов: {total_q}.\n"
        f"Чаще совпадали в теме: {top(match_constructs)}.\n"
        f"Чаще расходились в теме: {top(diverge_constructs)}.\n"
        f"Детали совпадений: {match_constructs}\n"
        f"Детали расхождений: {diverge_constructs}"
    )


# --- онбординг / профиль ---

FIELD_BY_CATEGORY = {
    "values": "core_values",
    "love_language": "love_language",
    "attachment": "attachment_style",
    "conflict": "conflict_style",
    "intimacy": "intimacy_views",
    "household": "core_values",  # быт тоже кладём в ценности/приоритеты
}


async def get_or_create_profile(session: AsyncSession, telegram_id: int) -> UserProfile:
    profile = await session.get(UserProfile, telegram_id)
    if profile is None:
        profile = UserProfile(user_id=telegram_id)
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
    return profile


async def profile_is_completed(session: AsyncSession, telegram_id: int) -> bool:
    profile = await session.get(UserProfile, telegram_id)
    return bool(profile and profile.is_completed)


def _load_answers_dict(profile: UserProfile) -> dict[str, Any]:
    try:
        data = json.loads(profile.raw_answers_json or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def save_onboarding_answer(
    session: AsyncSession,
    telegram_id: int,
    *,
    step: int,
    question_id: str,
    question_text: str,
    answer_text: str,
    category: str,
    next_step: int,
) -> UserProfile:
    """Сохраняет один ответ анкеты в SQLite и двигает onboarding_step."""
    profile = await get_or_create_profile(session, telegram_id)
    answers = _load_answers_dict(profile)
    answers[question_id] = {
        "q": question_text,
        "a": answer_text,
        "category": category,
        "step": step,
    }
    profile.raw_answers_json = json.dumps(answers, ensure_ascii=False)

    if category == "gender" or question_id == "gender":
        from gender_utils import gender_from_answer_text

        profile.gender = gender_from_answer_text(answer_text)
    else:
        field = FIELD_BY_CATEGORY.get(category)
        if field:
            prev = getattr(profile, field) or ""
            chunk = f"{answer_text}"
            setattr(profile, field, (prev + " | " + chunk).strip(" |") if prev else chunk)

    profile.onboarding_step = next_step
    profile.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(profile)
    return profile


async def save_followup_questions(
    session: AsyncSession, telegram_id: int, questions: list[str]
) -> UserProfile:
    profile = await get_or_create_profile(session, telegram_id)
    profile.followup_json = json.dumps(questions[:3], ensure_ascii=False)
    profile.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(profile)
    return profile


async def save_followup_answer(
    session: AsyncSession,
    telegram_id: int,
    *,
    index: int,
    question_text: str,
    answer_text: str,
    next_step: int,
) -> UserProfile:
    profile = await get_or_create_profile(session, telegram_id)
    answers = _load_answers_dict(profile)
    answers[f"followup_{index}"] = {"q": question_text, "a": answer_text, "category": "followup"}
    profile.raw_answers_json = json.dumps(answers, ensure_ascii=False)
    profile.onboarding_step = next_step
    profile.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(profile)
    return profile


async def complete_profile(
    session: AsyncSession,
    telegram_id: int,
    ai_summary: str,
    *,
    ai_summary_public: str | None = None,
    ai_summary_private: str | None = None,
) -> UserProfile:
    profile = await get_or_create_profile(session, telegram_id)
    profile.ai_summary = (ai_summary or "").strip() or None
    profile.ai_summary_public = (ai_summary_public or "").strip() or None
    profile.ai_summary_private = (ai_summary_private or "").strip() or None
    profile.is_completed = True
    profile.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(profile)
    return profile


async def mark_onboarding_done_pending_summary(
    session: AsyncSession, telegram_id: int
) -> UserProfile:
    """
    Анкета заполнена (gating открыт), но портрет Люма ещё не готов.
    ai_summary=None → можно повторить генерацию позже.
    """
    profile = await get_or_create_profile(session, telegram_id)
    profile.is_completed = True
    profile.ai_summary = None
    profile.ai_summary_public = None
    profile.ai_summary_private = None
    profile.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(profile)
    return profile


async def reset_onboarding(session: AsyncSession, telegram_id: int) -> UserProfile:
    """Полный сброс анкеты (для настроек Mini App / /onboarding)."""
    profile = await get_or_create_profile(session, telegram_id)
    profile.onboarding_step = 0
    profile.is_completed = False
    profile.raw_answers_json = "{}"
    profile.followup_json = "[]"
    profile.ai_summary = None
    profile.ai_summary_public = None
    profile.ai_summary_private = None
    profile.core_values = ""
    profile.love_language = ""
    profile.attachment_style = ""
    profile.conflict_style = ""
    profile.intimacy_views = ""
    profile.gender = None
    profile.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(profile)
    return profile


def profile_answers_for_ai(profile: UserProfile) -> dict[str, str]:
    """Компактный dict для промптов (без лишнего объёма)."""
    base, followup = profile_answers_split(profile)
    # Совместимость: всё в одном dict (база + уточнения)
    out = dict(base)
    out.update(followup)
    return out


def profile_answers_split(profile: UserProfile) -> tuple[dict[str, str], dict[str, str]]:
    """
    Делит raw_answers_json на базовые (15) и follow-up (3).
    Возвращает (base_answers, followup_answers) как {вопрос: ответ}.
    """
    raw = _load_answers_dict(profile)
    base: dict[str, str] = {}
    followup: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            q = str(v.get("q") or k)[:200]
            a = str(v.get("a") or "")[:400]
            cat = str(v.get("category") or "")
            is_fu = str(k).startswith("followup_") or cat == "followup"
        else:
            q = str(k)[:200]
            a = str(v)[:400]
            is_fu = str(k).startswith("followup_")
        if not a.strip():
            continue
        if is_fu:
            followup[q] = a
        else:
            base[q] = a
    return base, followup


def parse_blocked_topics(raw: str | None) -> list[str]:
    """'routine,fun' → ['routine', 'fun']."""
    if not raw:
        return []
    out: list[str] = []
    for part in str(raw).split(","):
        code = part.strip().lower()
        if code and code not in out:
            out.append(code)
    return out


async def couple_blocked_topics(session: AsyncSession) -> list[str]:
    """Объединение блокировок обоих партнёров — тема скрыта для всей пары."""
    union: list[str] = []
    for tg_id in config.partner_ids():
        profile = await session.get(UserProfile, tg_id)
        if not profile:
            continue
        for code in parse_blocked_topics(profile.blocked_topics):
            if code not in union:
                union.append(code)
    return union


async def add_blocked_topic(
    session: AsyncSession, telegram_id: int, topic_code: str
) -> UserProfile:
    profile = await get_or_create_profile(session, telegram_id)
    codes = parse_blocked_topics(profile.blocked_topics)
    code = (topic_code or "").strip().lower()
    if code and code not in codes and code != "surprise":
        codes.append(code)
        profile.blocked_topics = ",".join(codes)
        profile.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(profile)
    return profile


async def remove_blocked_topic(
    session: AsyncSession, telegram_id: int, topic_code: str
) -> UserProfile:
    profile = await get_or_create_profile(session, telegram_id)
    code = (topic_code or "").strip().lower()
    codes = [c for c in parse_blocked_topics(profile.blocked_topics) if c != code]
    profile.blocked_topics = ",".join(codes)
    profile.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(profile)
    return profile


async def clear_blocked_topics(session: AsyncSession, telegram_id: int) -> UserProfile:
    profile = await get_or_create_profile(session, telegram_id)
    profile.blocked_topics = ""
    profile.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(profile)
    return profile


async def save_weekly_digest(
    session: AsyncSession,
    *,
    user_id_1: int,
    user_id_2: int,
    pattern: str,
    actions: list[str] | list[dict[str, Any]],
    raw_json: str,
    formatted_text: str,
    week_start: datetime | None = None,
    week_end: datetime | None = None,
    pattern_short: str | None = None,
    quiz_count: int = 0,
    avg_temperature: float | None = None,
    sent_at: datetime | None = None,
) -> WeeklyDigest:
    """Сохраняет итоги недели в SQLite."""
    action_objs: list[dict[str, Any]] = []
    for a in (actions or [])[:5]:
        if isinstance(a, dict):
            action_objs.append(
                {
                    "text": str(a.get("text") or "").strip()[:400],
                    "done": bool(a.get("done")),
                }
            )
        else:
            action_objs.append({"text": str(a).strip()[:400], "done": False})

    row = WeeklyDigest(
        user_id_1=user_id_1,
        user_id_2=user_id_2 or 0,
        week_start=week_start,
        week_end=week_end,
        pattern=(pattern or "").strip()[:2000],
        pattern_short=((pattern_short or "").strip()[:200] or None),
        actions_json=json.dumps(action_objs, ensure_ascii=False),
        quiz_count=int(quiz_count or 0),
        avg_temperature=avg_temperature,
        sent_at=sent_at,
        raw_json=(raw_json or "{}")[:8000],
        formatted_text=(formatted_text or "")[:8000],
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def parse_digest_actions(raw: str | None) -> list[dict[str, Any]]:
    """Читает actions_json → список {text, done}."""
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            out.append(
                {
                    "text": str(item.get("text") or "").strip(),
                    "done": bool(item.get("done")),
                }
            )
        elif isinstance(item, str) and item.strip():
            out.append({"text": item.strip(), "done": False})
    return out


def week_bounds_utc(ref: datetime | None = None) -> tuple[datetime, datetime]:
    """Понедельник 00:00 UTC — воскресенье 23:59:59 UTC текущей недели."""
    now = ref or datetime.utcnow()
    monday = now.date() - timedelta(days=now.weekday())
    week_start = datetime(monday.year, monday.month, monday.day)
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return week_start, week_end


async def get_digest_for_week(
    session: AsyncSession, week_start: datetime
) -> Optional[WeeklyDigest]:
    return await session.scalar(
        select(WeeklyDigest)
        .where(WeeklyDigest.week_start == week_start)
        .order_by(WeeklyDigest.id.desc())
        .limit(1)
    )


async def get_latest_digest(session: AsyncSession) -> Optional[WeeklyDigest]:
    return await session.scalar(
        select(WeeklyDigest).order_by(WeeklyDigest.week_start.desc(), WeeklyDigest.id.desc()).limit(1)
    )


async def list_digests(session: AsyncSession, limit: int = 10) -> list[WeeklyDigest]:
    rows = (
        await session.execute(
            select(WeeklyDigest)
            .order_by(WeeklyDigest.week_start.desc(), WeeklyDigest.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def set_digest_action_done(
    session: AsyncSession,
    digest_id: int,
    action_index: int,
    done: bool,
) -> Optional[list[dict[str, Any]]]:
    row = await session.get(WeeklyDigest, digest_id)
    if not row:
        return None
    actions = parse_digest_actions(row.actions_json)
    if action_index < 0 or action_index >= len(actions):
        return None
    actions[action_index]["done"] = bool(done)
    row.actions_json = json.dumps(actions, ensure_ascii=False)
    await session.commit()
    return actions


async def save_digest_reply(
    session: AsyncSession,
    *,
    telegram_id: int,
    user_text: str,
    ai_reply: str = "",
    digest_id: int | None = None,
) -> DigestReply:
    """Сохраняет ответ на вопрос после дайджеста."""
    row = DigestReply(
        telegram_id=telegram_id,
        digest_id=digest_id,
        user_text=(user_text or "").strip()[:2000],
        ai_reply=(ai_reply or "").strip()[:4000],
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def update_digest_reply_ai(
    session: AsyncSession, reply_id: int, ai_reply: str
) -> None:
    row = await session.get(DigestReply, reply_id)
    if not row:
        return
    row.ai_reply = (ai_reply or "").strip()[:4000]
    await session.commit()


# --- Date ideas ---

_IDEA_STATUSES = frozenset({"proposed", "saved", "done", "dismissed"})


def serialize_date_idea(row: DateIdea) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title or "",
        "description": row.description or "",
        "category": row.category,
        "duration_hint": row.duration_hint,
        "budget_hint": row.budget_hint,
        "status": row.status or "proposed",
        "digest_id": row.digest_id,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


async def list_date_ideas(
    session: AsyncSession,
    *,
    statuses: list[str] | None = None,
) -> list[DateIdea]:
    allowed = statuses or ["proposed", "saved", "done"]
    allowed = [s for s in allowed if s in _IDEA_STATUSES]
    if not allowed:
        allowed = ["proposed", "saved", "done"]
    rows = (
        await session.execute(
            select(DateIdea)
            .where(DateIdea.status.in_(allowed))
            .order_by(DateIdea.created_at.desc(), DateIdea.id.desc())
        )
    ).scalars().all()
    return list(rows)


async def save_date_ideas(
    session: AsyncSession,
    ideas: list[dict[str, Any]],
    *,
    digest_id: int | None = None,
    status: str = "proposed",
) -> list[DateIdea]:
    now = datetime.utcnow()
    rows: list[DateIdea] = []
    for item in ideas[:3]:
        row = DateIdea(
            title=str(item.get("title") or "").strip()[:200],
            description=str(item.get("description") or "").strip()[:2000],
            category=(str(item.get("category") or "").strip()[:50] or None),
            duration_hint=(str(item.get("duration_hint") or "").strip()[:50] or None),
            budget_hint=(str(item.get("budget_hint") or "").strip()[:50] or None),
            status=status if status in _IDEA_STATUSES else "proposed",
            digest_id=digest_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        rows.append(row)
    await session.commit()
    for row in rows:
        await session.refresh(row)
    return rows


async def update_date_idea_status(
    session: AsyncSession, idea_id: int, status: str
) -> Optional[DateIdea]:
    if status not in _IDEA_STATUSES:
        return None
    row = await session.get(DateIdea, idea_id)
    if not row:
        return None
    row.status = status
    row.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(row)
    return row


async def delete_proposed_date_ideas(session: AsyncSession) -> int:
    rows = (
        await session.execute(select(DateIdea).where(DateIdea.status == "proposed"))
    ).scalars().all()
    n = len(rows)
    for row in rows:
        await session.delete(row)
    if n:
        await session.commit()
    return n


async def latest_proposed_idea_created_at(
    session: AsyncSession,
) -> Optional[datetime]:
    row = await session.scalar(
        select(DateIdea)
        .where(DateIdea.status == "proposed")
        .order_by(DateIdea.created_at.desc())
        .limit(1)
    )
    return row.created_at if row else None


async def list_done_idea_titles(
    session: AsyncSession, *, days: int = 90
) -> list[str]:
    """Заголовки done-идей за последние N дней."""
    since = datetime.utcnow() - timedelta(days=max(1, days))
    rows = (
        await session.execute(
            select(DateIdea)
            .where(DateIdea.status == "done", DateIdea.updated_at >= since)
            .order_by(DateIdea.updated_at.desc())
            .limit(40)
        )
    ).scalars().all()
    return [str(r.title or "").strip() for r in rows if (r.title or "").strip()]


async def delete_date_idea(session: AsyncSession, idea_id: int) -> bool:
    row = await session.get(DateIdea, idea_id)
    if not row:
        return False
    await session.delete(row)
    await session.commit()
    return True


# --- Private user notes ---


def serialize_user_note(row: UserNote, quiz: Quiz | None = None) -> dict[str, Any]:
    topic = None
    if quiz is not None:
        topic = (
            config.mood_label(quiz.topic_code)
            if quiz.topic_code
            else (quiz.topic or None)
        )
    return {
        "id": row.id,
        "text": row.text or "",
        "quiz_id": row.quiz_id,
        "quiz_topic": topic,
        "quiz_date": (
            (quiz.created_at.isoformat() + "Z") if quiz and quiz.created_at else None
        ),
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


async def list_user_notes(
    session: AsyncSession, user_id: int, *, limit: int = 30
) -> list[tuple[UserNote, Quiz | None]]:
    lim = max(1, min(int(limit or 30), 100))
    notes = (
        await session.execute(
            select(UserNote)
            .where(UserNote.user_id == user_id)
            .order_by(UserNote.created_at.desc(), UserNote.id.desc())
            .limit(lim)
        )
    ).scalars().all()
    out: list[tuple[UserNote, Quiz | None]] = []
    for note in notes:
        quiz = await session.get(Quiz, note.quiz_id) if note.quiz_id else None
        out.append((note, quiz))
    return out


async def create_user_note(
    session: AsyncSession,
    *,
    user_id: int,
    text: str,
    quiz_id: int | None = None,
) -> UserNote:
    now = datetime.utcnow()
    row = UserNote(
        user_id=user_id,
        text=text,
        quiz_id=quiz_id,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def update_user_note(
    session: AsyncSession, *, note_id: int, user_id: int, text: str
) -> Optional[UserNote]:
    row = await session.get(UserNote, note_id)
    if not row or row.user_id != user_id:
        return None
    row.text = text
    row.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(row)
    return row


async def delete_user_note(
    session: AsyncSession, *, note_id: int, user_id: int
) -> bool:
    row = await session.get(UserNote, note_id)
    if not row or row.user_id != user_id:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def list_note_texts_for_user(
    session: AsyncSession, user_id: int, *, days: int = 7
) -> list[str]:
    since = datetime.utcnow() - timedelta(days=max(1, days))
    rows = (
        await session.execute(
            select(UserNote)
            .where(UserNote.user_id == user_id, UserNote.created_at >= since)
            .order_by(UserNote.created_at.desc())
            .limit(40)
        )
    ).scalars().all()
    return [str(r.text or "").strip()[:500] for r in rows if (r.text or "").strip()]


# --- Hidden questions (private topic requests) ---


def serialize_hidden_question(row: HiddenQuestion) -> dict[str, Any]:
    return {
        "id": row.id,
        "text": row.text or "",
        "status": row.status or "pending",
        "used_in_quiz_id": row.used_in_quiz_id,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "used_at": row.used_at.isoformat() + "Z" if row.used_at else None,
    }


async def list_hidden_questions(
    session: AsyncSession, user_id: int, *, limit: int = 50
) -> list[HiddenQuestion]:
    lim = max(1, min(int(limit or 50), 100))
    rows = (
        await session.execute(
            select(HiddenQuestion)
            .where(HiddenQuestion.user_id == user_id)
            .order_by(
                # pending first, then used, then by date
                HiddenQuestion.status.asc(),
                HiddenQuestion.created_at.desc(),
                HiddenQuestion.id.desc(),
            )
            .limit(lim)
        )
    ).scalars().all()
    # Stable UX order: pending by created_at ASC (queue), used by used_at DESC
    pending = [r for r in rows if (r.status or "") == "pending"]
    used = [r for r in rows if (r.status or "") == "used"]
    other = [r for r in rows if (r.status or "") not in ("pending", "used")]
    pending.sort(key=lambda r: (r.created_at or datetime.min, r.id))
    used.sort(key=lambda r: (r.used_at or r.created_at or datetime.min, r.id), reverse=True)
    return pending + used + other


async def count_pending_hidden_questions(session: AsyncSession, user_id: int) -> int:
    rows = (
        await session.execute(
            select(HiddenQuestion).where(
                HiddenQuestion.user_id == user_id,
                HiddenQuestion.status == "pending",
            )
        )
    ).scalars().all()
    return len(rows)


async def create_hidden_question(
    session: AsyncSession, *, user_id: int, text: str
) -> HiddenQuestion:
    row = HiddenQuestion(
        user_id=user_id,
        text=text,
        status="pending",
        created_at=datetime.utcnow(),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def delete_hidden_question(
    session: AsyncSession, *, question_id: int, user_id: int
) -> bool:
    row = await session.get(HiddenQuestion, question_id)
    if not row or row.user_id != user_id:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def get_pending_hidden_question(
    session: AsyncSession, user_id: int
) -> Optional[HiddenQuestion]:
    """Самый старый pending-вопрос пользователя."""
    return await session.scalar(
        select(HiddenQuestion)
        .where(
            HiddenQuestion.user_id == user_id,
            HiddenQuestion.status == "pending",
        )
        .order_by(HiddenQuestion.created_at.asc(), HiddenQuestion.id.asc())
        .limit(1)
    )


async def pick_pending_hidden_for_couple(
    session: AsyncSession, user_ids: list[int]
) -> Optional[HiddenQuestion]:
    """Самый старый pending среди любых партнёров пары."""
    ids = [int(x) for x in (user_ids or []) if x]
    if not ids:
        return None
    return await session.scalar(
        select(HiddenQuestion)
        .where(
            HiddenQuestion.user_id.in_(ids),
            HiddenQuestion.status == "pending",
        )
        .order_by(HiddenQuestion.created_at.asc(), HiddenQuestion.id.asc())
        .limit(1)
    )


async def mark_hidden_question_used(
    session: AsyncSession,
    *,
    question_id: int,
    quiz_id: int,
) -> Optional[HiddenQuestion]:
    row = await session.get(HiddenQuestion, question_id)
    if not row:
        return None
    row.status = "used"
    row.used_in_quiz_id = quiz_id
    row.used_at = datetime.utcnow()
    await session.commit()
    await session.refresh(row)
    return row


# --- Discussion (private per user × quiz) ---

DISCUSSION_META_CLOSED = "closed"


async def save_discussion_message(
    session: AsyncSession,
    *,
    quiz_id: int,
    user_id: int,
    role: str,
    text: str,
) -> DiscussionMessage:
    row = DiscussionMessage(
        quiz_id=int(quiz_id),
        user_id=int(user_id),
        role=(role or "user").strip()[:20],
        text=(text or "").strip()[:8000],
        created_at=datetime.utcnow(),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_discussion_history(
    session: AsyncSession,
    quiz_id: int,
    user_id: int,
    *,
    limit: int = 20,
    include_meta: bool = False,
) -> list[DiscussionMessage]:
    lim = max(1, min(int(limit or 20), 50))
    rows = (
        await session.execute(
            select(DiscussionMessage)
            .where(
                DiscussionMessage.quiz_id == int(quiz_id),
                DiscussionMessage.user_id == int(user_id),
            )
            .order_by(DiscussionMessage.created_at.desc(), DiscussionMessage.id.desc())
            .limit(lim * 2 if not include_meta else lim)
        )
    ).scalars().all()
    ordered = list(reversed(rows))
    if include_meta:
        return ordered[-lim:]
    visible = [r for r in ordered if (r.role or "") in ("user", "lum")]
    return visible[-lim:]


async def count_user_discussion_messages_24h(
    session: AsyncSession, quiz_id: int, user_id: int
) -> int:
    since = datetime.utcnow() - timedelta(hours=24)
    rows = (
        await session.execute(
            select(DiscussionMessage).where(
                DiscussionMessage.quiz_id == int(quiz_id),
                DiscussionMessage.user_id == int(user_id),
                DiscussionMessage.role == "user",
                DiscussionMessage.created_at >= since,
            )
        )
    ).scalars().all()
    return len(rows)


async def get_last_user_discussion_at(
    session: AsyncSession, quiz_id: int, user_id: int
) -> Optional[datetime]:
    row = await session.scalar(
        select(DiscussionMessage)
        .where(
            DiscussionMessage.quiz_id == int(quiz_id),
            DiscussionMessage.user_id == int(user_id),
            DiscussionMessage.role == "user",
        )
        .order_by(DiscussionMessage.created_at.desc())
        .limit(1)
    )
    return row.created_at if row else None


async def is_discussion_active(
    session: AsyncSession, quiz_id: int, user_id: int
) -> bool:
    """Активен, если есть сообщения и нет meta=closed после последнего user/lum."""
    rows = (
        await session.execute(
            select(DiscussionMessage)
            .where(
                DiscussionMessage.quiz_id == int(quiz_id),
                DiscussionMessage.user_id == int(user_id),
            )
            .order_by(DiscussionMessage.created_at.desc(), DiscussionMessage.id.desc())
            .limit(30)
        )
    ).scalars().all()
    if not rows:
        return False
    for r in rows:
        if (r.role or "") == "meta" and (r.text or "").strip() == DISCUSSION_META_CLOSED:
            return False
        if (r.role or "") in ("user", "lum"):
            return True
    return False


async def close_discussion(
    session: AsyncSession, quiz_id: int, user_id: int
) -> None:
    await save_discussion_message(
        session,
        quiz_id=quiz_id,
        user_id=user_id,
        role="meta",
        text=DISCUSSION_META_CLOSED,
    )


def serialize_discussion_message(row: DiscussionMessage) -> dict[str, Any]:
    return {
        "role": row.role,
        "text": row.text or "",
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
    }
