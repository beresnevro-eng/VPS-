#!/usr/bin/env python3
"""Роли доступа к боту: admin (полный) и limited (ссылки + новые ключи)."""
from __future__ import annotations

import json
from pathlib import Path

DIR = Path(__file__).resolve().parent
CHAT_FILE = DIR / "tg_chat_id"
LIMITED_FILE = DIR / "limited_users.json"

Role = str  # "admin" | "limited" | "denied"


def _norm_username(username: str | None) -> str:
    return (username or "").strip().lower().lstrip("@")


def load_limited_users() -> dict:
    if not LIMITED_FILE.is_file():
        return {"users": []}
    try:
        return json.loads(LIMITED_FILE.read_text())
    except Exception:
        return {"users": []}


def save_limited_users(data: dict) -> None:
    LIMITED_FILE.parent.mkdir(parents=True, exist_ok=True)
    LIMITED_FILE.write_text(json.dumps(data, indent=2) + "\n")
    try:
        LIMITED_FILE.chmod(0o600)
    except Exception:
        pass


def admin_chat_ids(cfg: dict[str, str]) -> set[int]:
    s: set[int] = set()
    if cfg.get("TG_CHAT_ID", "").strip().lstrip("-").isdigit():
        s.add(int(cfg["TG_CHAT_ID"].strip()))
    if CHAT_FILE.is_file():
        for line in CHAT_FILE.read_text().splitlines():
            t = line.strip()
            if t.lstrip("-").isdigit():
                s.add(int(t))
    return s


def limited_chat_ids() -> set[int]:
    out: set[int] = set()
    for u in load_limited_users().get("users", []):
        cid = u.get("chat_id")
        if cid is not None and str(cid).lstrip("-").isdigit():
            out.add(int(cid))
    return out


def all_allowed_chat_ids(cfg: dict[str, str]) -> set[int]:
    return admin_chat_ids(cfg) | limited_chat_ids()


def _bind_limited_chat_id(username: str, chat_id: int) -> bool:
    """Привязать chat_id к username при первом /start. True если новая привязка."""
    uname = _norm_username(username)
    if not uname:
        return False
    data = load_limited_users()
    changed = False
    for u in data.get("users", []):
        if _norm_username(u.get("username")) != uname:
            continue
        if u.get("chat_id") != chat_id:
            u["chat_id"] = chat_id
            changed = True
        break
    if changed:
        save_limited_users(data)
    return changed


def get_role(chat_id: int, username: str | None, cfg: dict[str, str]) -> Role:
    if chat_id in admin_chat_ids(cfg):
        return "admin"
    if chat_id in limited_chat_ids():
        return "limited"
    uname = _norm_username(username)
    for u in load_limited_users().get("users", []):
        if _norm_username(u.get("username")) == uname:
            _bind_limited_chat_id(username, chat_id)
            return "limited"
    return "denied"


def is_new_limited_bind(chat_id: int, username: str | None, cfg: dict[str, str]) -> bool:
    if get_role(chat_id, username, cfg) != "limited":
        return False
    uname = _norm_username(username)
    for u in load_limited_users().get("users", []):
        if _norm_username(u.get("username")) == uname and u.get("chat_id") == chat_id:
            return True
    return False


LIMITED_COMMANDS = frozenset({
    "/start", "/menu", "/help",
    "/links", "/newclient", "/addclient", "/qr",
})

LIMITED_CALLBACKS = frozenset({
    "m:main", "m:links", "m:help", "m:newclient",
})


def limited_callback_allowed(data: str) -> bool:
    if data in LIMITED_CALLBACKS:
        return True
    if data.startswith(("l:", "q:")):
        return True
    return False


def limited_command_allowed(cmd: str) -> bool:
    return cmd in LIMITED_COMMANDS
