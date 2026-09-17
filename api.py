"""
Лёгкий HTTP API для Telegram Mini App «Шёпот».
Хостится рядом с aiogram (uvicorn, 1 воркер) — без Gunicorn/Redis.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import parse_qsl

import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

import config
from database import (
    Quiz,
    UserProfile,
    WeeklyDigest,
    add_blocked_topic,
    calculate_streak,
    couple_blocked_topics,
    create_user_note,
    create_hidden_question,
    delete_proposed_date_ideas,
    delete_user_note,
    delete_hidden_question,
    get_collecting_quiz,
    get_latest_digest,
    get_session,
    get_user_by_telegram,
    latest_proposed_idea_created_at,
    list_date_ideas,
    list_digests,
    list_done_idea_titles,
    list_note_texts_for_user,
    list_user_notes,
    list_hidden_questions,
    count_pending_hidden_questions,
    load_quiz_for_analysis,
    parse_blocked_topics,
    parse_digest_actions,
    remove_blocked_topic,
    save_date_ideas,
    serialize_date_idea,
    serialize_user_note,
    serialize_hidden_question,
    set_digest_action_done,
    update_date_idea_status,
    update_user_note,
    update_notification_settings,
    serialize_notification_settings,
    user_finished_quiz,
    get_discussion_history,
    is_discussion_active,
    close_discussion,
    serialize_discussion_message,
    save_discussion_message,
)

logger = logging.getLogger(__name__)

DOCS_DIR = Path(__file__).resolve().parent / "docs"

app = FastAPI(
    title=f"{config.PROJECT_NAME} Mini App API",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(getattr(config, "API_CORS_ORIGINS", None) or [
        "https://beresnevro-eng.github.io",
        "https://beresnevro-eng.github.io/VPS-",
    ]),
    allow_origin_regex=r"https://(.*\.)?(github\.io|trycloudflare\.com|wspr\.online)",
    allow_credentials=False,  # cookies не нужны; так совместимее с WebView
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)


@app.options("/{full_path:path}")
async def preflight(full_path: str) -> dict[str, bool]:
    """Явный preflight для Telegram WebView / некоторых прокси."""
    return {"ok": True}


# --- Telegram WebApp initData validation (HMAC-SHA256) ---


def validate_webapp_init_data(init_data: str, bot_token: str) -> dict[str, Any]:
    """
    Проверка подписи initData по документации Telegram Web Apps.
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data or not bot_token:
        raise HTTPException(status_code=401, detail="initData отсутствует")

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Нет hash в initData")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated, received_hash):
        raise HTTPException(status_code=401, detail="Неверная подпись initData")

    user_raw = parsed.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="Нет user в initData")
    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=401, detail="Битый user JSON") from exc

    user_id = int(user.get("id") or 0)
    if not user_id:
        raise HTTPException(status_code=401, detail="Нет user.id")

    auth_date = 0
    try:
        auth_date = int(parsed.get("auth_date") or 0)
        if auth_date and (datetime.utcnow().timestamp() - auth_date) > 86400:
            raise HTTPException(status_code=401, detail="initData устарел")
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Битый auth_date") from exc

    return {
        "user_id": user_id,
        "first_name": user.get("first_name") or "",
        "last_name": user.get("last_name") or "",
        "username": user.get("username") or "",
        "auth_date": auth_date,
    }


def couple_id_for(user_id: int) -> Optional[str]:
    """Стабильный id пары, если пользователь — один из партнёров."""
    ids = list(config.partner_ids())
    if user_id not in ids:
        return None
    if len(ids) < 2:
        return f"solo_{ids[0]}"
    a, b = sorted(ids)
    return f"{a}_{b}"


def _partner_of(user_id: int) -> str:
    """Имя второго партнёра (не текущего пользователя)."""
    if user_id == config.PARTNER_A_ID and config.PARTNER_B_ID:
        return config.PARTNER_B_NAME
    if user_id == config.PARTNER_B_ID and config.PARTNER_A_ID:
        return config.PARTNER_A_NAME
    if config.PARTNER_B_ID and user_id != config.PARTNER_B_ID:
        return config.PARTNER_B_NAME
    if config.PARTNER_A_ID and user_id != config.PARTNER_A_ID:
        return config.PARTNER_A_NAME
    return ""


def require_partner(user_id: int) -> None:
    if user_id not in config.allowed_user_ids():
        raise HTTPException(status_code=403, detail="Доступ только для пары Шёпота")


class AuthRequest(BaseModel):
    initData: str = Field(default="", description="Telegram WebApp initData")
    token: str = Field(default="", description="Подпись из URL кнопки бота")


class AuthResponse(BaseModel):
    user_id: int
    couple_id: Optional[str]
    name: str
    project: str
    assistant: str
    bot_username: str = ""


def _auth_info_from_token(token: str) -> dict[str, Any]:
    from mini_auth import verify_mini_app_token

    try:
        user_id = verify_mini_app_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    require_partner(user_id)
    return {
        "user_id": user_id,
        "username": None,
        "first_name": config.partner_name(user_id),
        "auth_via": "token",
    }


async def auth_from_header(
    x_telegram_init_data: Optional[str] = Header(default=None, alias="X-Telegram-Init-Data"),
    x_shepot_auth_token: Optional[str] = Header(default=None, alias="X-Shepot-Auth-Token"),
    initData: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    """Зависимость: initData из Telegram ИЛИ токен с кнопки бота."""
    if x_shepot_auth_token:
        return _auth_info_from_token(x_shepot_auth_token)
    raw = x_telegram_init_data or initData
    if not raw:
        raise HTTPException(
            status_code=401,
            detail="Нужен X-Telegram-Init-Data или X-Shepot-Auth-Token",
        )
    info = validate_webapp_init_data(raw, config.BOT_TOKEN)
    require_partner(info["user_id"])
    return info


@app.get("/health")
@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "project": config.PROJECT_NAME}


@app.post("/api/auth", response_model=AuthResponse)
async def api_auth(body: AuthRequest) -> AuthResponse:
    if (body.token or "").strip():
        info = _auth_info_from_token(body.token.strip())
    elif (body.initData or "").strip():
        info = validate_webapp_init_data(body.initData.strip(), config.BOT_TOKEN)
        require_partner(info["user_id"])
    else:
        raise HTTPException(status_code=401, detail="Нужен initData или token")

    user_id = info["user_id"]
    name = config.partner_name(user_id)
    return AuthResponse(
        user_id=user_id,
        couple_id=couple_id_for(user_id),
        name=name or info.get("first_name") or "",
        project=config.PROJECT_NAME,
        assistant=config.ASSISTANT_NAME,
        bot_username=config.BOT_USERNAME,
    )


@app.get("/api/history")
async def api_history(
    auth: dict[str, Any] = Depends(auth_from_header),
    limit: int = Query(default=10, ge=1, le=30),
) -> dict[str, Any]:
    """Последние квизы пары: дата, тема, ответы, анализ Люма."""
    user_id = auth["user_id"]
    async with get_session() as session:
        quizzes = (
            await session.execute(
                select(Quiz).order_by(Quiz.id.desc()).limit(limit)
            )
        ).scalars().all()

        items: list[dict[str, Any]] = []
        for quiz in quizzes:
            questions: list[Any] = []
            try:
                payload = await load_quiz_for_analysis(session, quiz.id)
                questions = payload.get("questions") or []
            except Exception:
                logger.exception("history quiz %s", quiz.id)
            items.append(
                {
                    "quiz_id": quiz.id,
                    "date": (quiz.created_at or datetime.utcnow()).isoformat() + "Z",
                    "topic": quiz.topic,
                    "topic_code": quiz.topic_code or "",
                    "topic_label": config.mood_label(quiz.topic_code)
                    if quiz.topic_code
                    else (quiz.topic or ""),
                    "status": quiz.status,
                    "analysis": quiz.analysis_text or "",
                    "questions": questions,
                    "discuss_url": f"https://t.me/{config.BOT_USERNAME}?start=discuss_{quiz.id}",
                }
            )

    return {
        "user_id": user_id,
        "couple_id": couple_id_for(user_id),
        "count": len(items),
        "quizzes": items,
    }


@app.get("/api/profile")
async def api_profile(
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    """Портрет пользователя + публичная часть портрета партнёра + блокировки."""
    user_id = auth["user_id"]
    partner_id = _partner_telegram_id(user_id)

    async with get_session() as session:
        profile = await session.get(UserProfile, user_id)
        user = await get_user_by_telegram(session, user_id)
        partner_payload = await _partner_profile_payload(session, partner_id)

        if not profile:
            return {
                "user_id": user_id,
                "name": config.partner_name(user_id),
                "partner_name": _partner_of(user_id),
                "partner": partner_payload,
                "is_completed": False,
                "ai_summary": None,
                "ai_summary_public": None,
                "ai_summary_private": None,
                "gender": None,
                "blocked_topics": [],
                "blocked_labels": [],
                "couple_id": couple_id_for(user_id),
                "bot_username": config.BOT_USERNAME,
                "notifications": serialize_notification_settings(None),
            }
        blocked = parse_blocked_topics(profile.blocked_topics)
        return {
            "user_id": user_id,
            "name": (user.name if user else "") or config.partner_name(user_id),
            "partner_name": _partner_of(user_id),
            "partner": partner_payload,
            "is_completed": bool(profile.is_completed),
            "ai_summary": profile.ai_summary,
            "ai_summary_public": profile.ai_summary_public,
            "ai_summary_private": profile.ai_summary_private,
            "gender": profile.gender,
            "blocked_topics": blocked,
            "blocked_labels": [config.mood_label(c) for c in blocked],
            "couple_id": couple_id_for(user_id),
            "bot_username": config.BOT_USERNAME,
            "updated_at": profile.updated_at.isoformat() + "Z" if profile.updated_at else None,
            "notifications": serialize_notification_settings(profile),
        }


class OnboardingAnswerRequest(BaseModel):
    step: int = Field(..., ge=0, le=20)
    answer: str = Field(default="", max_length=2000)


@app.get("/api/onboarding/state")
async def api_onboarding_state(
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    """Текущий шаг анкеты Mini App (15 база + 3 AI-уточнения)."""
    from onboarding_service import get_onboarding_state

    return await get_onboarding_state(int(auth["user_id"]))


@app.post("/api/onboarding/answer")
async def api_onboarding_answer(
    body: OnboardingAnswerRequest,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    from onboarding_service import submit_onboarding_answer

    try:
        return await submit_onboarding_answer(
            int(auth["user_id"]),
            step=int(body.step),
            answer=body.answer,
            skipped=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/onboarding/skip")
async def api_onboarding_skip(
    body: OnboardingAnswerRequest,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    """Пропуск текущего вопроса (обычно open_ended)."""
    from onboarding_service import submit_onboarding_answer

    try:
        return await submit_onboarding_answer(
            int(auth["user_id"]),
            step=int(body.step),
            answer=None,
            skipped=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/onboarding/reset")
async def api_onboarding_reset(
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    """Сброс анкеты. Только осознанно (настройки + подтверждение на клиенте)."""
    from onboarding_service import reset_onboarding_state

    return await reset_onboarding_state(int(auth["user_id"]))


def _partner_telegram_id(user_id: int) -> Optional[int]:
    if user_id == config.PARTNER_A_ID and config.PARTNER_B_ID:
        return config.PARTNER_B_ID
    if user_id == config.PARTNER_B_ID and config.PARTNER_A_ID:
        return config.PARTNER_A_ID
    ids = [i for i in config.partner_ids() if i != user_id]
    return ids[0] if ids else None


def _serialize_streak(raw: dict[str, Any]) -> dict[str, Any]:
    last = raw.get("last_quiz_at")
    return {
        "current_streak": int(raw.get("current_streak") or 0),
        "longest_streak": int(raw.get("longest_streak") or 0),
        "last_quiz_at": (last.isoformat() + "Z") if last else None,
        "days_since_last": int(raw.get("days_since_last") or 0),
    }


def _compact_streak(raw: dict[str, Any]) -> dict[str, int]:
    return {
        "current_streak": int(raw.get("current_streak") or 0),
        "longest_streak": int(raw.get("longest_streak") or 0),
    }


async def _load_streak(session) -> dict[str, Any]:
    return await calculate_streak(session, list(config.partner_ids()))


async def _partner_profile_payload(session, partner_id: Optional[int]) -> dict[str, Any]:
    if not partner_id:
        return {
            "user_id": None,
            "name": "",
            "is_completed": False,
            "ai_summary_public": None,
            "has_private": False,
            "gender": None,
        }
    profile = await session.get(UserProfile, partner_id)
    user = await get_user_by_telegram(session, partner_id)
    name = (user.name if user else "") or config.partner_name(partner_id)
    if not profile:
        return {
            "user_id": partner_id,
            "name": name,
            "is_completed": False,
            "ai_summary_public": None,
            "has_private": False,
            "gender": None,
        }
    public = (profile.ai_summary_public or "").strip() or None
    # Старые портреты без split: не отдаём полный текст партнёру
    if not public and profile.ai_summary:
        public = None
    has_private = bool((profile.ai_summary_private or "").strip()) or bool(
        profile.ai_summary and not profile.ai_summary_public
    )
    return {
        "user_id": partner_id,
        "name": name,
        "is_completed": bool(profile.is_completed),
        "ai_summary_public": public,
        "has_private": has_private,
        "gender": profile.gender,
        "updated_at": profile.updated_at.isoformat() + "Z" if profile.updated_at else None,
    }


class QuizStartRequest(BaseModel):
    mood_code: str = Field(..., min_length=2, max_length=32)


class TopicBlockRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=32)
    blocked: bool = True


def _flat_answer_text(a: dict[str, Any]) -> str:
    if a.get("skipped"):
        return ""
    selected = (a.get("selected_option") or "").strip()
    if selected:
        return selected
    return str(a.get("text") or "").strip()


def _map_quiz_questions(raw_questions: list[Any], auth_user_id: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for q in raw_questions or []:
        answers = []
        for a in q.get("answers") or []:
            tg = int(a.get("telegram_id") or 0)
            answers.append(
                {
                    "user_id": tg,
                    "name": a.get("name") or config.partner_name(tg) or "?",
                    "answer": _flat_answer_text(a),
                    "skipped": bool(a.get("skipped")),
                }
            )
        out.append(
            {
                "question": q.get("text") or "",
                "answers": answers,
            }
        )
    return out


@app.get("/api/moods")
async def api_moods(
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    """Каталог настроений + какие темы закрыты у пары."""
    async with get_session() as session:
        blocked = await couple_blocked_topics(session)
    blocked_set = set(blocked)
    moods = []
    for code, meta in config.MOOD_CATALOG.items():
        moods.append(
            {
                "code": code,
                "label": meta["label"],
                "blocked": code != "surprise" and code in blocked_set,
            }
        )
    return {"moods": moods}


@app.get("/api/streak")
async def api_streak(
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    """Текущая и рекордная серия завершённых квизов пары."""
    _ = auth  # auth required
    async with get_session() as session:
        raw = await _load_streak(session)
    return _serialize_streak(raw)


@app.get("/api/quiz/active")
async def api_quiz_active(
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    """Текущий активный квиз + человеческий status_text + статусы партнёров."""
    user_id = auth["user_id"]
    partner_id = _partner_telegram_id(user_id)
    partner_name = _partner_of(user_id) or "партнёр"

    async with get_session() as session:
        streak_raw = await _load_streak(session)
        streak = _compact_streak(streak_raw)

        quiz = await get_collecting_quiz(session)
        if not quiz:
            # exchanging / analysis_pending — оба ответили, Люм готовит разбор
            quiz = await session.scalar(
                select(Quiz)
                .where(Quiz.status.in_(("exchanging", "analysis_pending")))
                .order_by(Quiz.id.desc())
                .limit(1)
            )
        if not quiz:
            return {
                "has_active": False,
                "status_text": "Можно начать новый разговор",
                "you_answered": False,
                "partner_answered": False,
                "analysis_pending": False,
                "streak": streak,
            }

        you_done = await user_finished_quiz(session, quiz.id, user_id)
        partner_done = (
            await user_finished_quiz(session, quiz.id, partner_id) if partner_id else False
        )
        analysis_pending = quiz.status in ("exchanging", "analysis_pending") or (
            you_done and partner_done
        )

        partners = []
        for tg in config.partner_ids():
            answered = await user_finished_quiz(session, quiz.id, tg)
            partners.append(
                {
                    "user_id": tg,
                    "name": config.partner_name(tg),
                    "answered": answered,
                }
            )

        if analysis_pending:
            status_text = "Люм готовит разбор"
        elif not you_done and not partner_done:
            status_text = "Ждём ответа обоих"
        elif you_done and not partner_done:
            # Имя в именительном: без винительного («Ждём Юля»)
            status_text = f"Ждём, пока {partner_name} ответит"
        elif not you_done and partner_done:
            from gender_utils import gendered

            answered = gendered(
                partner_id,
                "ответил",
                "ответила",
                "ответил(а)",
            )
            status_text = f"{partner_name} уже {answered} · ждём тебя"
        else:
            status_text = "Люм готовит разбор"

        topic_label = (
            config.mood_label(quiz.topic_code)
            if quiz.topic_code
            else (quiz.topic or "")
        )
        return {
            "has_active": True,
            "quiz_id": quiz.id,
            "topic": topic_label,
            "topic_code": quiz.topic_code or "",
            "status": quiz.status,
            "status_text": status_text,
            "you_answered": you_done,
            "partner_answered": partner_done,
            "analysis_pending": analysis_pending,
            "started_at": (quiz.created_at or datetime.utcnow()).isoformat() + "Z",
            "partners": partners,
            "streak": streak,
        }


@app.post("/api/quiz/start")
async def api_quiz_start(
    body: QuizStartRequest,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    """Запуск внеочередного квиза через start_quiz_for_pair (тот же движок, что бот)."""
    code = (body.mood_code or "").strip().lower()
    if code not in config.MOOD_CATALOG:
        raise HTTPException(status_code=400, detail="Неизвестное настроение")

    async with get_session() as session:
        active = await get_collecting_quiz(session)
        if active:
            raise HTTPException(
                status_code=409,
                detail="У вас уже есть активный квиз. Закончите его в чате с ботом.",
            )
        blocked = await couple_blocked_topics(session)

    if code != "surprise" and code in blocked:
        raise HTTPException(
            status_code=400,
            detail="Тема закрыта, выберите другую",
        )

    from handlers import _pick_auto_topic_code, start_quiz_for_pair  # noqa: WPS433
    import runtime as runtime_mod  # noqa: WPS433

    resolved = _pick_auto_topic_code(blocked) if code == "surprise" else code
    if not resolved:
        raise HTTPException(
            status_code=400,
            detail="Почти все темы закрыты. Откройте часть тем в разделе «Мы».",
        )

    try:
        bot = runtime_mod.get_bot()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Бот ещё не готов") from exc

    try:
        quiz = await start_quiz_for_pair(
            bot,
            resolved,
            automatic=False,
            notify_chat=auth["user_id"],
            close_active=False,
        )
    except RuntimeError as exc:
        if str(exc) == "active_quiz_exists":
            raise HTTPException(
                status_code=409,
                detail="У вас уже есть активный квиз. Закончите его в чате с ботом.",
            ) from exc
        logger.exception("quiz/start RuntimeError")
        raise HTTPException(status_code=500, detail="Не удалось запустить квиз") from exc
    except ValueError as exc:
        msg = str(exc)
        if msg == "blocked_topic":
            raise HTTPException(
                status_code=400,
                detail="Тема закрыта, выберите другую",
            ) from exc
        raise HTTPException(status_code=400, detail=msg) from exc
    except Exception as exc:
        logger.exception("quiz/start failed user=%s mood=%s", auth["user_id"], resolved)
        raise HTTPException(status_code=500, detail="Не удалось запустить квиз") from exc

    return {
        "ok": True,
        "quiz_id": quiz.id,
        "message": "Квиз отправлен в чат с Люмом 🌿",
    }


@app.get("/api/quiz/{quiz_id}")
async def api_quiz_detail(
    quiz_id: int,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    """Детали квиза: вопросы, плоские ответы, анализ Люма."""
    user_id = auth["user_id"]
    async with get_session() as session:
        quiz = await session.get(Quiz, quiz_id)
        if not quiz:
            raise HTTPException(status_code=404, detail="Квиз не найден")
        try:
            payload = await load_quiz_for_analysis(session, quiz_id)
        except Exception as exc:
            logger.exception("quiz detail %s", quiz_id)
            raise HTTPException(status_code=500, detail="Не удалось загрузить квиз") from exc

    topic_label = (
        config.mood_label(quiz.topic_code) if quiz.topic_code else (quiz.topic or "")
    )
    return {
        "quiz_id": quiz.id,
        "date": (quiz.created_at or datetime.utcnow()).isoformat() + "Z",
        "topic": topic_label,
        "topic_code": quiz.topic_code or "",
        "status": quiz.status,
        "questions": _map_quiz_questions(payload.get("questions") or [], user_id),
        "analysis": quiz.analysis_text or "",
        "is_mine": True,  # одна пара на бота; квиз принадлежит паре текущего user
        "discuss_url": f"https://t.me/{config.BOT_USERNAME}?start=discuss_{quiz.id}",
    }


class DiscussionPostRequest(BaseModel):
    text: Optional[str] = Field(default=None, max_length=2000)
    done: Optional[bool] = None


@app.get("/api/quiz/{quiz_id}/discussion")
async def api_quiz_discussion_get(
    quiz_id: int,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    user_id = auth["user_id"]
    async with get_session() as session:
        quiz = await session.get(Quiz, quiz_id)
        if not quiz:
            raise HTTPException(status_code=404, detail="Квиз не найден")
        rows = await get_discussion_history(session, quiz_id, user_id, limit=50)
        active = await is_discussion_active(session, quiz_id, user_id)
    return {
        "quiz_id": quiz_id,
        "messages": [serialize_discussion_message(r) for r in rows],
        "is_active": active,
    }


@app.post("/api/quiz/{quiz_id}/discussion")
async def api_quiz_discussion_post(
    quiz_id: int,
    body: DiscussionPostRequest,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    user_id = auth["user_id"]
    async with get_session() as session:
        quiz = await session.get(Quiz, quiz_id)
        if not quiz:
            raise HTTPException(status_code=404, detail="Квиз не найден")
        analysis = (quiz.analysis_text or "").strip()
        topic = (
            config.mood_label(quiz.topic_code)
            if quiz.topic_code in config.MOOD_CATALOG
            else (quiz.topic or "")
        )
        created_at = quiz.created_at

    if body.done:
        bye = (
            "🌿 Спасибо за разговор. Если захочешь вернуться — "
            "просто открой квиз в Истории."
        )
        async with get_session() as session:
            await save_discussion_message(
                session, quiz_id=quiz_id, user_id=user_id, role="lum", text=bye
            )
            await close_discussion(session, quiz_id, user_id)
        return {"reply": bye, "done": True}

    text = (body.text or "").strip()
    if len(text) < 1:
        raise HTTPException(status_code=400, detail="Пустое сообщение")

    async with get_session() as session:
        hist = await get_discussion_history(session, quiz_id, user_id, limit=3)
        active = await is_discussion_active(session, quiz_id, user_id)
        if not hist or not active:
            from handlers import _brief_analysis  # noqa: WPS433

            brief = _brief_analysis(analysis)
            date_s = (created_at or datetime.utcnow()).strftime("%d.%m.%Y")
            if brief:
                greet = (
                    f"🌿 Давай обсудим квиз «{topic}» от {date_s}.\n"
                    f"Вот что я заметил: {brief}\n"
                    "Что тебя в этом зацепило больше всего?"
                )
            else:
                greet = (
                    f"🌿 Давай обсудим квиз «{topic}» от {date_s}.\n"
                    "Что тебя зацепило больше всего?"
                )
            await save_discussion_message(
                session, quiz_id=quiz_id, user_id=user_id, role="lum", text=greet
            )

    from handlers import process_discussion_turn  # noqa: WPS433

    reply, err = await process_discussion_turn(
        quiz_id=quiz_id,
        user_id=user_id,
        user_text=text,
        name=config.partner_name(user_id),
        topic=topic,
        analysis=analysis,
    )
    if err == "rate_limit":
        raise HTTPException(status_code=429, detail=reply)
    return {"reply": reply, "error": err}


@app.post("/api/topics/block")
async def api_topics_block(
    body: TopicBlockRequest,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    """Блок / анблок темы для текущего пользователя (учитывается на уровне пары)."""
    code = (body.code or "").strip().lower()
    if code not in config.MOOD_CATALOG or code == "surprise":
        raise HTTPException(status_code=400, detail="Эту тему нельзя закрыть")

    user_id = auth["user_id"]
    async with get_session() as session:
        if body.blocked:
            await add_blocked_topic(session, user_id, code)
        else:
            await remove_blocked_topic(session, user_id, code)
        profile = await session.get(UserProfile, user_id)
        blocked_topics = parse_blocked_topics(profile.blocked_topics if profile else "")
        couple_blocked = await couple_blocked_topics(session)

    return {
        "ok": True,
        "code": code,
        "blocked": body.blocked,
        "blocked_topics": blocked_topics,
        "couple_blocked": couple_blocked,
    }


# --- Private notes ---


class NoteCreateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    quiz_id: Optional[int] = None


class NoteUpdateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


def _validate_note_text(raw: str) -> str:
    text = (raw or "").strip()
    if len(text) < 3:
        raise HTTPException(status_code=400, detail="Заметка слишком короткая (минимум 3 символа)")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="Заметка слишком длинная (максимум 2000 символов)")
    return text


@app.get("/api/notes")
async def api_notes_list(
    auth: dict[str, Any] = Depends(auth_from_header),
    limit: int = Query(default=30, ge=1, le=100),
) -> dict[str, Any]:
    user_id = auth["user_id"]
    async with get_session() as session:
        rows = await list_user_notes(session, user_id, limit=limit)
        notes = [serialize_user_note(note, quiz) for note, quiz in rows]
    return {"notes": notes, "count": len(notes)}


@app.post("/api/notes")
async def api_notes_create(
    body: NoteCreateRequest,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    user_id = auth["user_id"]
    text = _validate_note_text(body.text)
    quiz_id = body.quiz_id
    async with get_session() as session:
        quiz = None
        if quiz_id is not None:
            quiz = await session.get(Quiz, int(quiz_id))
            if not quiz:
                raise HTTPException(status_code=404, detail="Квиз не найден")
        note = await create_user_note(
            session, user_id=user_id, text=text, quiz_id=quiz_id
        )
        if quiz_id and quiz is None:
            quiz = await session.get(Quiz, quiz_id)
        return serialize_user_note(note, quiz)


@app.put("/api/notes/{note_id}")
async def api_notes_update(
    note_id: int,
    body: NoteUpdateRequest,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    user_id = auth["user_id"]
    text = _validate_note_text(body.text)
    async with get_session() as session:
        note = await update_user_note(
            session, note_id=note_id, user_id=user_id, text=text
        )
        if not note:
            raise HTTPException(status_code=404, detail="Заметка не найдена")
        quiz = await session.get(Quiz, note.quiz_id) if note.quiz_id else None
        return serialize_user_note(note, quiz)


@app.delete("/api/notes/{note_id}")
async def api_notes_delete(
    note_id: int,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    user_id = auth["user_id"]
    async with get_session() as session:
        ok = await delete_user_note(session, note_id=note_id, user_id=user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return {"ok": True}


# --- Hidden questions (private) ---


class HiddenQuestionCreateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=400)


def _validate_hidden_question_text(raw: str) -> str:
    text = (raw or "").strip()
    if len(text) < 5:
        raise HTTPException(
            status_code=400, detail="Слишком коротко (минимум 5 символов)"
        )
    if len(text) > 200:
        raise HTTPException(
            status_code=400, detail="Слишком длинно (максимум 200 символов)"
        )
    return text


@app.get("/api/hidden-questions")
async def api_hidden_questions_list(
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    user_id = auth["user_id"]
    async with get_session() as session:
        rows = await list_hidden_questions(session, user_id)
        questions = [serialize_hidden_question(r) for r in rows]
    logger.info(
        "GET /api/hidden-questions user_id=%s returned %s items",
        user_id,
        len(questions),
    )
    return {"questions": questions}


@app.post("/api/hidden-questions")
async def api_hidden_questions_create(
    body: HiddenQuestionCreateRequest,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    user_id = auth["user_id"]
    text = _validate_hidden_question_text(body.text)
    async with get_session() as session:
        pending = await count_pending_hidden_questions(session, user_id)
        if pending >= 5:
            raise HTTPException(
                status_code=400,
                detail=(
                    "У вас уже 5 активных вопросов. "
                    "Дождитесь, пока Люм задаст один из них."
                ),
            )
        row = await create_hidden_question(session, user_id=user_id, text=text)
        return serialize_hidden_question(row)


@app.delete("/api/hidden-questions/{question_id}")
async def api_hidden_questions_delete(
    question_id: int,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    user_id = auth["user_id"]
    async with get_session() as session:
        ok = await delete_hidden_question(
            session, question_id=question_id, user_id=user_id
        )
    if not ok:
        raise HTTPException(status_code=404, detail="Вопрос не найден")
    return {"ok": True}


# --- Notification settings ---


class NotificationSettingsRequest(BaseModel):
    preferred_hour: Optional[int] = Field(default=None, ge=0, le=23)
    reactivation_enabled: Optional[bool] = None
    analysis_notify_enabled: Optional[bool] = None


@app.post("/api/notifications/settings")
async def api_notifications_settings(
    body: NotificationSettingsRequest,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    user_id = auth["user_id"]
    payload = body.model_dump(exclude_unset=True)
    if not payload:
        async with get_session() as session:
            profile = await session.get(UserProfile, user_id)
            return {"notifications": serialize_notification_settings(profile)}
    try:
        async with get_session() as session:
            profile = await update_notification_settings(
                session,
                user_id,
                preferred_hour=payload.get("preferred_hour"),
                reactivation_enabled=payload.get("reactivation_enabled"),
                analysis_notify_enabled=payload.get("analysis_notify_enabled"),
            )
            return {"notifications": serialize_notification_settings(profile)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Date ideas ---


class IdeaStatusRequest(BaseModel):
    status: str = Field(..., min_length=3, max_length=20)


@app.get("/api/ideas")
async def api_ideas_list(
    auth: dict[str, Any] = Depends(auth_from_header),
    status: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    """Идеи по группам: proposed / saved / done."""
    _ = auth
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
    else:
        statuses = ["proposed", "saved", "done"]
    async with get_session() as session:
        rows = await list_date_ideas(session, statuses=statuses)
    grouped: dict[str, list[dict[str, Any]]] = {
        "proposed": [],
        "saved": [],
        "done": [],
    }
    for r in rows:
        st = (r.status or "proposed").strip().lower()
        if st not in grouped:
            continue
        grouped[st].append(serialize_date_idea(r))
    return {
        "ideas": grouped,
        "count": {k: len(v) for k, v in grouped.items()},
    }


@app.post("/api/ideas/{idea_id}/status")
async def api_ideas_status(
    idea_id: int,
    body: IdeaStatusRequest,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    _ = auth
    status = (body.status or "").strip().lower()
    if status not in {"saved", "done", "dismissed", "proposed"}:
        raise HTTPException(status_code=400, detail="Недопустимый статус")
    async with get_session() as session:
        row = await update_date_idea_status(session, idea_id, status)
    if not row:
        raise HTTPException(status_code=404, detail="Идея не найдена")
    return {"idea": serialize_date_idea(row)}


@app.delete("/api/ideas/{idea_id}")
async def api_ideas_delete(
    idea_id: int,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    """Удаляет идею пары полностью."""
    _ = auth  # доступ уже ограничен парой через auth_from_header
    async with get_session() as session:
        from database import DateIdea  # noqa: WPS433

        row = await session.get(DateIdea, idea_id)
        if not row:
            raise HTTPException(status_code=404, detail="Идея не найдена")
        await session.delete(row)
        await session.commit()
    return {"ok": True}


async def _profiles_for_ideas(session) -> dict[str, Any]:
    partners = list(config.partner_ids())
    out: dict[str, Any] = {}
    for key, tg in zip(("A", "B"), partners):
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


@app.post("/api/ideas/regenerate")
async def api_ideas_regenerate(
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    """Новые 3 proposed-идеи (старые proposed удаляются). Не чаще 1 раза / 24ч."""
    _ = auth
    import ai_service  # noqa: WPS433

    async with get_session() as session:
        last_at = await latest_proposed_idea_created_at(session)
        if last_at and (datetime.utcnow() - last_at).total_seconds() < 24 * 3600:
            raise HTTPException(
                status_code=429,
                detail="Новые идеи можно запросить раз в 24 часа",
            )
        digest = await get_latest_digest(session)
        profiles = await _profiles_for_ideas(session)
        blocked = await couple_blocked_topics(session)
        digest_payload = {
            "pattern": (digest.pattern if digest else "") or "",
            "pattern_short": (digest.pattern_short if digest else "") or "",
        }
        digest_id = digest.id if digest else None
        done_titles = await list_done_idea_titles(session, days=90)
        partners_ids = list(config.partner_ids())
        idea_notes_a = (
            await list_note_texts_for_user(session, partners_ids[0], days=30)
            if partners_ids
            else []
        )
        idea_notes_b = (
            await list_note_texts_for_user(session, partners_ids[1], days=30)
            if len(partners_ids) > 1
            else []
        )

    ideas_raw = await ai_service.generate_date_ideas(
        profiles,
        digest_payload,
        blocked,
        done_ideas_titles=done_titles,
        notes_a=idea_notes_a,
        notes_b=idea_notes_b,
    )
    if not ideas_raw:
        raise HTTPException(status_code=502, detail="Люм не смог придумать идеи. Попробуйте позже.")

    async with get_session() as session:
        await delete_proposed_date_ideas(session)
        rows = await save_date_ideas(session, ideas_raw, digest_id=digest_id)
        ideas = [serialize_date_idea(r) for r in rows]
    return {
        "ideas": {
            "proposed": ideas,
            "saved": [],
            "done": [],
        },
        "count": {"proposed": len(ideas), "saved": 0, "done": 0},
    }


# --- Weekly digest ---


def _iso_z(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.isoformat() + ("Z" if not str(dt).endswith("Z") else "")


def _serialize_digest_full(row: WeeklyDigest) -> dict[str, Any]:
    avg = row.avg_temperature
    return {
        "id": row.id,
        "week_start": _iso_z(row.week_start),
        "week_end": _iso_z(row.week_end),
        "pattern": row.pattern or "",
        "pattern_short": row.pattern_short or "",
        "actions": parse_digest_actions(row.actions_json),
        "quiz_count": int(row.quiz_count or 0),
        "avg_temperature": round(float(avg), 1) if avg is not None else None,
        "created_at": _iso_z(row.created_at),
        "sent_at": _iso_z(row.sent_at),
    }


def _serialize_digest_brief(row: WeeklyDigest) -> dict[str, Any]:
    return {
        "id": row.id,
        "week_start": _iso_z(row.week_start),
        "week_end": _iso_z(row.week_end),
        "pattern": row.pattern or "",
        "pattern_short": row.pattern_short or "",
        "quiz_count": int(row.quiz_count or 0),
        "avg_temperature": (
            round(float(row.avg_temperature), 1)
            if row.avg_temperature is not None
            else None
        ),
        "created_at": _iso_z(row.created_at),
    }


class DigestActionRequest(BaseModel):
    digest_id: int = Field(..., ge=1)
    action_index: int = Field(..., ge=0, le=10)
    done: bool = True


@app.get("/api/digest/latest")
async def api_digest_latest(
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    _ = auth
    async with get_session() as session:
        row = await get_latest_digest(session)
    if not row:
        return {"digest": None}
    return {"digest": _serialize_digest_full(row)}


@app.get("/api/digest/history")
async def api_digest_history(
    auth: dict[str, Any] = Depends(auth_from_header),
    limit: int = Query(default=10, ge=1, le=30),
) -> dict[str, Any]:
    _ = auth
    async with get_session() as session:
        rows = await list_digests(session, limit=limit)
    # history без текущего latest actions — brief, без actions
    items = [_serialize_digest_brief(r) for r in rows]
    # исключим дубль latest из «прошлых» на фронте при желании
    return {"digests": items}


@app.post("/api/digest/action")
async def api_digest_action(
    body: DigestActionRequest,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    _ = auth
    async with get_session() as session:
        actions = await set_digest_action_done(
            session, body.digest_id, body.action_index, body.done
        )
    if actions is None:
        raise HTTPException(status_code=404, detail="Дайджест или действие не найдены")
    return {"ok": True, "actions": actions}


# --- Insights / dynamics dashboard ---


@app.get("/api/insights/trends")
async def api_insights_trends(
    auth: dict[str, Any] = Depends(auth_from_header),
    period: int = Query(default=30, ge=7, le=90, alias="period"),
) -> dict[str, Any]:
    """Температура отношений по разобранным квизам за period дней."""
    _ = auth
    since = datetime.utcnow() - timedelta(days=int(period))
    async with get_session() as session:
        quizzes = (
            await session.execute(
                select(Quiz)
                .where(
                    Quiz.status == "analyzed",
                    Quiz.temperature_score.is_not(None),
                    Quiz.created_at >= since,
                )
                .order_by(Quiz.created_at.asc(), Quiz.id.asc())
            )
        ).scalars().all()

    points: list[dict[str, Any]] = []
    temps: list[float] = []
    for q in quizzes:
        score = int(q.temperature_score)  # type: ignore[arg-type]
        temps.append(float(score))
        dt = q.analyzed_at or q.created_at or datetime.utcnow()
        points.append(
            {
                "date": dt.strftime("%Y-%m-%d"),
                "temperature": score,
                "quiz_id": q.id,
            }
        )

    n = len(temps)
    avg = round(sum(temps) / n, 1) if n else 0.0
    if n < 2:
        direction = "flat"
        delta = 0.0
    else:
        mid = n // 2
        first = sum(temps[:mid]) / mid if mid else temps[0]
        second = sum(temps[mid:]) / (n - mid)
        delta = round(second - first, 1)
        if abs(delta) < 0.3:
            direction = "flat"
        elif delta > 0:
            direction = "up"
        else:
            direction = "down"

    return {
        "period_days": int(period),
        "points": points,
        "avg_temperature": avg,
        "trend_direction": direction,
        "trend_delta": delta,
    }


@app.get("/api/insights/topics")
async def api_insights_topics(
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    """Тепловая карта тем: совпадение и средняя температура."""
    _ = auth
    async with get_session() as session:
        quizzes = (
            await session.execute(
                select(Quiz).where(Quiz.status == "analyzed").order_by(Quiz.id.desc())
            )
        ).scalars().all()

    buckets: dict[str, dict[str, Any]] = {}
    for q in quizzes:
        code = (q.topic_code or "").strip() or "unknown"
        if code == "surprise":
            continue
        b = buckets.setdefault(
            code,
            {"match_rates": [], "temps": [], "count": 0},
        )
        b["count"] += 1
        qc = int(q.question_count or 0)
        mc = q.match_count
        if qc > 0 and mc is not None:
            b["match_rates"].append(float(mc) / float(qc))
        if q.temperature_score is not None:
            b["temps"].append(float(q.temperature_score))

    topics: list[dict[str, Any]] = []
    for code, b in buckets.items():
        rates = b["match_rates"]
        temps = b["temps"]
        topics.append(
            {
                "code": code,
                "label": config.mood_label(code) if code in config.MOOD_CATALOG else code,
                "quiz_count": int(b["count"]),
                "avg_match_rate": round(sum(rates) / len(rates), 2) if rates else 0.0,
                "avg_temperature": round(sum(temps) / len(temps), 1) if temps else 0.0,
            }
        )
    topics.sort(key=lambda t: (-t["quiz_count"], t["label"]))
    return {"topics": topics}


# --- Mini App UI (тот же хост, что API — без редиректа GitHub Pages) ---


@app.get("/app")
@app.get("/app/")
@app.get("/app/index.html")
async def mini_app_shell() -> FileResponse:
    return FileResponse(DOCS_DIR / "index.html", media_type="text/html; charset=utf-8")


@app.get("/app/{asset_path:path}")
async def mini_app_asset(asset_path: str) -> FileResponse:
    """Отдаём css/js рядом с index без mount (чтобы /app/index.html не перехватывался)."""
    safe = Path(asset_path).name
    target = DOCS_DIR / safe
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    media = "text/css" if safe.endswith(".css") else "application/javascript" if safe.endswith(".js") else None
    return FileResponse(target, media_type=media)
