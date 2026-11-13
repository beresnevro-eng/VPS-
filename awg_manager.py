#!/usr/bin/env python3
"""AmneziaWG helpers for Telegram bot (dual RU entries)."""
from __future__ import annotations

import subprocess
from pathlib import Path

AWG_PORT = 51830
AWG_CLIENTS_DIR_NEW = Path("/root/hop-amnesiawg-clients")
AWG_CLIENTS_DIR_OLD = Path("/root/hop-amnesiawg-clients-old")
AWG_CLIENTS_DIR_LOCAL = Path("/root/amnesiawg-clients")
AWG_SERVER_CONF = Path("/etc/amnezia/amneziawg/awg0.conf")
AWG_INFO = Path("/root/amnesiawg-info.txt")
AWG_INFO_HOP = Path("/root/hop-amnesiawg-info.txt")
AWG_UNIT = "awg-quick@awg0"

PROFILE_ALIASES: dict[str, str] = {
    "iphone": "iphone",
    "айфон": "iphone",
    "wife": "wife",
    "жена": "wife",
    "router": "router",
    "роутер": "router",
    "mac": "macbook",
    "macbook": "macbook",
    "vpn-a": "router",
    "all": "all",
    "все": "all",
}


def _run(cmd: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _dir_for(which: str = "new") -> Path:
    if which in ("old", "ru-old", "ruvds-old", "standby"):
        if AWG_CLIENTS_DIR_OLD.is_dir() and any(AWG_CLIENTS_DIR_OLD.glob("*.conf")):
            return AWG_CLIENTS_DIR_OLD
    if AWG_CLIENTS_DIR_NEW.is_dir() and any(AWG_CLIENTS_DIR_NEW.glob("*.conf")):
        return AWG_CLIENTS_DIR_NEW
    return AWG_CLIENTS_DIR_LOCAL


def _parse_profile(profile: str) -> tuple[str, str]:
    """Return (which_entry, profile_name). Supports 'old wife', 'wife-old'."""
    key = (profile or "").strip().lower().replace("_", "-")
    which = "new"
    parts = key.split()
    if len(parts) >= 2 and parts[0] in ("old", "new", "ru-old", "ruvds-old", "standby"):
        which = "old" if parts[0] != "new" else "new"
        key = parts[1]
    elif key.endswith("-old") or key.endswith(".old"):
        which = "old"
        key = key.rsplit("-", 1)[0].rsplit(".", 1)[0]
    elif key.startswith("old-"):
        which = "old"
        key = key[4:]
    name = PROFILE_ALIASES.get(key, key)
    return which, name


def awg_installed() -> bool:
    return bool(list_client_names()) or bool(list_client_names("old")) or AWG_SERVER_CONF.is_file()


def awg_service_state() -> str:
    return "RuVDS + RuVDS-old (конфиги на боте)"


def awg_active() -> bool:
    return awg_installed()


def list_client_names(which: str = "new") -> list[str]:
    d = _dir_for(which)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.conf"))


def client_config_path(profile: str) -> Path | None:
    which, name = _parse_profile(profile)
    if name in ("all", "все", "list", "help", "помощь", "status", "статус"):
        return None
    path = _dir_for(which) / f"{name}.conf"
    return path if path.is_file() else None


def format_awg_status() -> str:
    new = ", ".join(list_client_names("new")) or "—"
    old = ", ".join(list_client_names("old")) or "—"
    return "\n".join([
        "🛡 AmneziaWG",
        f"RuVDS (:51830): {new}",
        f"RuVDS-old (:51830): {old}",
        "/awg wife · /awg old wife · /awg iphone",
    ])


def format_awg_help() -> str:
    return (
        "🛡 <b>AmneziaWG</b> — два entry\n\n"
        "<b>RuVDS (новый)</b>\n"
        "/awg iphone · /awg wife · /awg router\n\n"
        "<b>RuVDS-old</b>\n"
        "/awg old iphone · /awg old wife · /awg old router\n\n"
        "Клиент: <b>AmneziaVPN</b> → + → импорт .conf\n"
        "На LTE МТС часто стабильнее Happ/Reality."
    )
