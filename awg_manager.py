#!/usr/bin/env python3
"""AmneziaWG helpers for Telegram bot (параллельный UDP-туннель)."""
from __future__ import annotations

import subprocess
from pathlib import Path

AWG_PORT = 51830
# Предпочитаем конфиги RU-entry hop; fallback — локальный Fornex AWG
AWG_CLIENTS_DIR_HOP = Path("/root/hop-amnesiawg-clients")
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


def _clients_dir() -> Path:
    if AWG_CLIENTS_DIR_HOP.is_dir() and any(AWG_CLIENTS_DIR_HOP.glob("*.conf")):
        return AWG_CLIENTS_DIR_HOP
    return AWG_CLIENTS_DIR_LOCAL


AWG_CLIENTS_DIR = AWG_CLIENTS_DIR_HOP  # default for docs; runtime uses _clients_dir()


def _run(cmd: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def awg_installed() -> bool:
    return bool(list_client_names()) or AWG_SERVER_CONF.is_file()


def awg_service_state() -> str:
    # Hop-конфиги лежат на Fornex-боте, сервис крутится на RU entry
    if _clients_dir() == AWG_CLIENTS_DIR_HOP:
        return "RU-entry (конфиги синхронизированы)"
    if not AWG_SERVER_CONF.is_file():
        return "not installed"
    r = _run(["systemctl", "is-active", AWG_UNIT], timeout=8)
    return (r.stdout or r.stderr or "unknown").strip()


def awg_active() -> bool:
    state = awg_service_state()
    return state == "active" or state.startswith("RU-entry")


def list_client_names() -> list[str]:
    d = _clients_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.conf"))


def client_config_path(profile: str) -> Path | None:
    key = profile.lower().strip()
    if key in ("all", "все", "list"):
        return None
    name = PROFILE_ALIASES.get(key, key)
    path = _clients_dir() / f"{name}.conf"
    return path if path.is_file() else None


def format_awg_status() -> str:
    if not awg_installed():
        return (
            "🛡 AmneziaWG: не установлен\n"
            f"UDP порт (план): {AWG_PORT}\n"
            "Установка на RU entry: bash /root/setup-amnesiawg-lite.sh"
        )
    clients = ", ".join(list_client_names()) or "—"
    lines = [
        "🛡 AmneziaWG (RU-entry :51830)",
        f"Статус: {awg_service_state()}",
        f"Профили: {clients}",
        "Конфиг: /awg iphone · /awg wife · /awg router",
    ]
    info = AWG_INFO_HOP if AWG_INFO_HOP.is_file() else AWG_INFO
    if info.is_file():
        try:
            first = info.read_text(errors="ignore").splitlines()[0].strip()
            if first:
                lines.insert(1, first)
        except Exception:
            pass
    return "\n".join(lines)


def format_awg_help() -> str:
    names = list_client_names()
    if not names:
        return (
            "AmneziaWG ещё не развёрнут.\n\n"
            "После установки на RU entry: /awg iphone"
        )
    return (
        "🛡 <b>AmneziaWG</b> — RU-entry UDP (для МТС Москва)\n\n"
        "Команды:\n"
        "/awg iphone — твой iPhone\n"
        "/awg wife — iPhone жены\n"
        "/awg router — роутер\n"
        "/awg status — статус\n\n"
        f"Доступно: {', '.join(names)}\n\n"
        "Клиент: <b>AmneziaVPN</b> → импорт .conf\n"
        "Exit сейчас через RU entry IP (обход DPI до сервера)."
    )
