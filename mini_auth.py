"""Подпись одноразового входа в Mini App (когда Telegram не отдаёт initData)."""

from __future__ import annotations

import hashlib
import hmac
import time

import config


def make_mini_app_token(user_id: int, ttl_sec: int = 30 * 24 * 3600) -> str:
    """HMAC-токен user_id:exp:sig — живёт в URL кнопки клавиатуры."""
    exp = int(time.time()) + int(ttl_sec)
    payload = f"{int(user_id)}:{exp}"
    sig = hmac.new(
        config.BOT_TOKEN.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"{payload}:{sig}"


def verify_mini_app_token(token: str) -> int:
    """Возвращает telegram user_id или бросает ValueError."""
    raw = (token or "").strip()
    parts = raw.split(":")
    if len(parts) != 3:
        raise ValueError("Некорректный токен")
    user_s, exp_s, sig = parts
    try:
        user_id = int(user_s)
        exp = int(exp_s)
    except ValueError as exc:
        raise ValueError("Некорректный токен") from exc
    if exp < int(time.time()):
        raise ValueError("Токен устарел — нажмите /menu в боте")
    payload = f"{user_id}:{exp}"
    expect = hmac.new(
        config.BOT_TOKEN.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    if not hmac.compare_digest(expect, sig):
        raise ValueError("Неверная подпись токена")
    return user_id
