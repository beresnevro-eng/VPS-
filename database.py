"""
Асинхронная работа с SQLite через SQLAlchemy 2.x.
Храним пользователей, квизы, вопросы и ответы.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
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
    # Все ответы анкеты (база + follow-up) как JSON-строка — не держим в RAM
    raw_answers_json: Mapped[str] = mapped_column(Text, default="{}")
    followup_json: Mapped[str] = mapped_column(Text, default="[]")  # 3 уточняющих вопроса
    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Коды заблокированных тем через запятую, напр. "routine,fun"
    blocked_topics: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WeeklyDigest(Base):
    """Итоги недели (паттерн + 3 действия) — лёгкая запись в SQLite."""

    __tablename__ = "weekly_digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id_1: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id_2: Mapped[int] = mapped_column(BigInteger, index=True, default=0)
    pattern: Mapped[str] = mapped_column(Text, default="")
    actions_json: Mapped[str] = mapped_column(Text, default="[]")
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    formatted_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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
        if "quizzes" in tables:
            z_cols = columns("quizzes")
            if "topic_code" not in z_cols:
                sync_conn.execute(
                    text("ALTER TABLE quizzes ADD COLUMN topic_code VARCHAR(32) DEFAULT ''")
                )

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
    return ans


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
    session: AsyncSession, telegram_id: int, ai_summary: str
) -> UserProfile:
    profile = await get_or_create_profile(session, telegram_id)
    profile.ai_summary = ai_summary.strip()
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
    actions: list[str],
    raw_json: str,
    formatted_text: str,
) -> WeeklyDigest:
    """Сохраняет итоги недели в SQLite."""
    row = WeeklyDigest(
        user_id_1=user_id_1,
        user_id_2=user_id_2 or 0,
        pattern=(pattern or "").strip()[:2000],
        actions_json=json.dumps(actions[:5], ensure_ascii=False),
        raw_json=(raw_json or "{}")[:8000],
        formatted_text=(formatted_text or "")[:8000],
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


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
