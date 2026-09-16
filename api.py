"""
Лёгкий HTTP API для Telegram Mini App «Шёпот».
Хостится рядом с aiogram (uvicorn, 1 воркер) — без Gunicorn/Redis.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime
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
    add_blocked_topic,
    couple_blocked_topics,
    get_collecting_quiz,
    get_session,
    get_user_by_telegram,
    load_quiz_for_analysis,
    parse_blocked_topics,
    remove_blocked_topic,
    user_finished_quiz,
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
    allow_origin_regex=r"https://.*\.(github\.io|trycloudflare\.com)",
    allow_credentials=False,  # cookies не нужны; так совместимее с WebView
    allow_methods=["GET", "POST", "OPTIONS"],
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
                    "discuss_url": f"https://t.me/Familia_Quiz_bot?start=discuss_{quiz.id}",
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
                "blocked_topics": [],
                "blocked_labels": [],
                "couple_id": couple_id_for(user_id),
                "bot_username": "Familia_Quiz_bot",
            }
        blocked = parse_blocked_topics(profile.blocked_topics)
        return {
            "user_id": user_id,
            "name": (user.name if user else "") or config.partner_name(user_id),
            "partner_name": _partner_of(user_id),
            "partner": partner_payload,
            "is_completed": bool(profile.is_completed),
            "ai_summary": profile.ai_summary,
            "blocked_topics": blocked,
            "blocked_labels": [config.mood_label(c) for c in blocked],
            "couple_id": couple_id_for(user_id),
            "bot_username": "Familia_Quiz_bot",
            "updated_at": profile.updated_at.isoformat() + "Z" if profile.updated_at else None,
        }


def _partner_telegram_id(user_id: int) -> Optional[int]:
    if user_id == config.PARTNER_A_ID and config.PARTNER_B_ID:
        return config.PARTNER_B_ID
    if user_id == config.PARTNER_B_ID and config.PARTNER_A_ID:
        return config.PARTNER_A_ID
    ids = [i for i in config.partner_ids() if i != user_id]
    return ids[0] if ids else None


async def _partner_profile_payload(session, partner_id: Optional[int]) -> dict[str, Any]:
    if not partner_id:
        return {
            "user_id": None,
            "name": "",
            "is_completed": False,
            "ai_summary": None,
        }
    profile = await session.get(UserProfile, partner_id)
    user = await get_user_by_telegram(session, partner_id)
    name = (user.name if user else "") or config.partner_name(partner_id)
    if not profile:
        return {
            "user_id": partner_id,
            "name": name,
            "is_completed": False,
            "ai_summary": None,
        }
    return {
        "user_id": partner_id,
        "name": name,
        "is_completed": bool(profile.is_completed),
        "ai_summary": profile.ai_summary,
        "updated_at": profile.updated_at.isoformat() + "Z" if profile.updated_at else None,
    }


class QuizStartRequest(BaseModel):
    mood_code: str = Field(..., min_length=2, max_length=32)


class TopicBlockRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=32)
    blocked: bool = True


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
                "blockable": code != "surprise",
            }
        )
    return {"moods": moods, "blocked": blocked}


@app.get("/api/quiz/active")
async def api_quiz_active(
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    """
    Статус текущего квиза для трёх CTA на Главной:
    - none → «Хочу обсудить сейчас»
    - answer → «Ответить в боте»
    - waiting → «Ждём партнёра» (пассивно)
    """
    user_id = auth["user_id"]
    partner_name = _partner_of(user_id) or "партнёра"

    async with get_session() as session:
        quiz = await get_collecting_quiz(session)
        if not quiz:
            return {
                "has_active": False,
                "cta": "start",
                "status_key": "none",
                "status_text": "Можно начать разговор",
                "quiz": None,
                "you_finished": False,
                "partner_finished": False,
                "bot_url": "https://t.me/Familia_Quiz_bot",
            }

        you_done = await user_finished_quiz(session, quiz.id, user_id)
        partner_id = _partner_telegram_id(user_id)
        partner_done = (
            await user_finished_quiz(session, quiz.id, partner_id) if partner_id else False
        )

        if you_done and not partner_done:
            cta = "waiting"
            status_key = "waiting_partner"
            status_text = f"Ждём {partner_name}"
        elif not you_done:
            cta = "answer"
            status_key = "need_answer"
            if partner_done:
                status_text = f"{partner_name} уже ответил(а) · ваш черёд"
            else:
                status_text = "Есть открытый квиз — продолжите в чате"
        else:
            # оба закончили, но статус ещё collecting на мгновение
            cta = "waiting"
            status_key = "finishing"
            status_text = "Люм готовит сравнение…"

        return {
            "has_active": True,
            "cta": cta,
            "status_key": status_key,
            "status_text": status_text,
            "you_finished": you_done,
            "partner_finished": partner_done,
            "bot_url": "https://t.me/Familia_Quiz_bot",
            "quiz": {
                "quiz_id": quiz.id,
                "topic": quiz.topic,
                "topic_code": quiz.topic_code or "",
                "topic_label": config.mood_label(quiz.topic_code)
                if quiz.topic_code
                else (quiz.topic or ""),
                "status": quiz.status,
                "date": (quiz.created_at or datetime.utcnow()).isoformat() + "Z",
                "discuss_url": f"https://t.me/Familia_Quiz_bot?start=discuss_{quiz.id}",
            },
        }


@app.post("/api/quiz/start")
async def api_quiz_start(
    body: QuizStartRequest,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    """Запуск внеочередного квиза (тот же движок, что /quiz + mood: в боте)."""
    code = (body.mood_code or "").strip().lower()
    if code not in config.MOOD_CATALOG:
        raise HTTPException(status_code=400, detail="Неизвестное настроение")

    async with get_session() as session:
        active = await get_collecting_quiz(session)
        if active:
            raise HTTPException(
                status_code=409,
                detail="Уже есть активный квиз. Сначала закончите его в чате с ботом.",
            )
        blocked = await couple_blocked_topics(session)

    if code != "surprise" and code in blocked:
        raise HTTPException(
            status_code=400,
            detail="Эта тема закрыта. Выберите другое настроение.",
        )

    # surprise → случайная незакрытая тема
    from handlers import _pick_auto_topic_code, start_daily_quiz  # noqa: WPS433
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

    # Фоном не нужно — дождёмся рассылки, чтобы клиент мог закрыть Mini App
    await start_daily_quiz(
        bot,
        topic=config.mood_prompt(resolved),
        topic_code=resolved,
        notify_chat=auth["user_id"],
        automatic=False,
    )

    return {
        "ok": True,
        "mood_code": resolved,
        "mood_label": config.mood_label(resolved),
        "message": "Квиз ушёл в чат с Люмом",
        "bot_url": "https://t.me/Familia_Quiz_bot",
    }


@app.get("/api/quiz/{quiz_id}")
async def api_quiz_detail(
    quiz_id: int,
    auth: dict[str, Any] = Depends(auth_from_header),
) -> dict[str, Any]:
    """Детали квиза: вопросы, ответы обоих, анализ Люма."""
    async with get_session() as session:
        quiz = await session.get(Quiz, quiz_id)
        if not quiz:
            raise HTTPException(status_code=404, detail="Квиз не найден")
        try:
            payload = await load_quiz_for_analysis(session, quiz_id)
        except Exception as exc:
            logger.exception("quiz detail %s", quiz_id)
            raise HTTPException(status_code=500, detail="Не удалось загрузить квиз") from exc

    return {
        "quiz_id": quiz.id,
        "date": (quiz.created_at or datetime.utcnow()).isoformat() + "Z",
        "topic": quiz.topic,
        "topic_code": quiz.topic_code or "",
        "topic_label": config.mood_label(quiz.topic_code)
        if quiz.topic_code
        else (quiz.topic or ""),
        "status": quiz.status,
        "analysis": quiz.analysis_text or "",
        "questions": payload.get("questions") or [],
        "discuss_url": f"https://t.me/Familia_Quiz_bot?start=discuss_{quiz.id}",
        "bot_url": "https://t.me/Familia_Quiz_bot",
    }


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
        blocked = await couple_blocked_topics(session)

    return {
        "ok": True,
        "code": code,
        "label": config.mood_label(code),
        "blocked": body.blocked,
        "couple_blocked": blocked,
    }


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
