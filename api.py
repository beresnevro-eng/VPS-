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

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select

import config
from database import (
    Quiz,
    UserProfile,
    get_session,
    get_user_by_telegram,
    load_quiz_for_analysis,
    parse_blocked_topics,
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title=f"{config.PROJECT_NAME} Mini App API",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://beresnevro-eng.github.io",
        "https://beresnevro-eng.github.io/VPS-",
    ],
    allow_origin_regex=r"https://.*\.github\.io",
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
    initData: str = Field(..., min_length=10)


class AuthResponse(BaseModel):
    user_id: int
    couple_id: Optional[str]
    name: str
    project: str
    assistant: str


async def auth_from_header(
    x_telegram_init_data: Optional[str] = Header(default=None, alias="X-Telegram-Init-Data"),
    initData: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    """Зависимость: валидирует initData из заголовка или query."""
    raw = x_telegram_init_data or initData
    if not raw:
        raise HTTPException(status_code=401, detail="Нужен X-Telegram-Init-Data или initData")
    info = validate_webapp_init_data(raw, config.BOT_TOKEN)
    require_partner(info["user_id"])
    return info


@app.get("/health")
@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "project": config.PROJECT_NAME}


@app.post("/api/auth", response_model=AuthResponse)
async def api_auth(body: AuthRequest) -> AuthResponse:
    info = validate_webapp_init_data(body.initData, config.BOT_TOKEN)
    user_id = info["user_id"]
    require_partner(user_id)
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
                select(Quiz)
                .where(Quiz.status.in_(("analyzed", "closed", "exchanging", "analysis_pending")))
                .order_by(Quiz.id.desc())
                .limit(limit)
            )
        ).scalars().all()

        items: list[dict[str, Any]] = []
        for quiz in quizzes:
            try:
                payload = await load_quiz_for_analysis(session, quiz.id)
            except Exception:
                logger.exception("history quiz %s", quiz.id)
                continue
            items.append(
                {
                    "quiz_id": quiz.id,
                    "date": (quiz.created_at or datetime.utcnow()).isoformat() + "Z",
                    "topic": quiz.topic,
                    "topic_code": quiz.topic_code or "",
                    "status": quiz.status,
                    "analysis": quiz.analysis_text or "",
                    "questions": payload.get("questions") or [],
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
    """Портрет UserProfile + блокировки тем."""
    user_id = auth["user_id"]
    async with get_session() as session:
        profile = await session.get(UserProfile, user_id)
        user = await get_user_by_telegram(session, user_id)
        if not profile:
            return {
                "user_id": user_id,
                "name": config.partner_name(user_id),
                "partner_name": _partner_of(user_id),
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
            "is_completed": bool(profile.is_completed),
            "ai_summary": profile.ai_summary,
            "blocked_topics": blocked,
            "blocked_labels": [config.mood_label(c) for c in blocked],
            "couple_id": couple_id_for(user_id),
            "bot_username": "Familia_Quiz_bot",
            "updated_at": profile.updated_at.isoformat() + "Z" if profile.updated_at else None,
        }
