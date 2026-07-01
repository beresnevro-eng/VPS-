#!/usr/bin/env python3
"""AmneziaWG helpers for Telegram bot (параллельный UDP-туннель)."""
from __future__ import annotations

import subprocess
from pathlib import Path

AWG_PORT = 51830
AWG_CLIENTS_DIR = Path("/root/amnesiawg-clients")
AWG_SERVER_CONF = Path("/etc/amnezia/amneziawg/awg0.conf")
AWG_INFO = Path("/root/amnesiawg-info.txt")
AWG_UNIT = "awg-quick@awg0"

PROFILE_ALIASES: dict[str, str] = {
    "iphone": "iphone",
    "айфон": "iphone",
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


def awg_installed() -> bool:
    return AWG_SERVER_CONF.is_file()


def awg_service_state() -> str:
    if not awg_installed():
        return "not installed"
    r = _run(["systemctl", "is-active", AWG_UNIT], timeout=8)
    return (r.stdout or r.stderr or "unknown").strip()


def awg_active() -> bool:
    return awg_service_state() == "active"


def list_client_names() -> list[str]:
    if not AWG_CLIENTS_DIR.is_dir():
        return []
    return sorted(p.stem for p in AWG_CLIENTS_DIR.glob("*.conf"))


def client_config_path(profile: str) -> Path | None:
    key = profile.lower().strip()
    if key in ("all", "все", "list"):
        return None
    name = PROFILE_ALIASES.get(key, key)
    path = AWG_CLIENTS_DIR / f"{name}.conf"
    return path if path.is_file() else None


def format_awg_status() -> str:
    if not awg_installed():
        return (
            "🛡 AmneziaWG: не установлен\n"
            f"UDP порт (план): {AWG_PORT}\n"
            "Установка: bash /root/setup-amnesiawg-lite.sh"
        )
    state = awg_service_state()
    clients = ", ".join(list_client_names()) or "—"
    lines = [
        "🛡 AmneziaWG",
        f"Сервис: {state}",
        f"UDP: {AWG_PORT}",
        f"Профили: {clients}",
        "Конфиг: /awg iphone · /awg router",
    ]
    if AWG_INFO.is_file():
        try:
            first = AWG_INFO.read_text(errors="ignore").splitlines()[0].strip()
            if first:
                lines.insert(1, first)
        except Exception:
            pass
    if awg_active():
        r = _run(["awg", "show", "awg0"], timeout=8)
        if r.stdout:
            peers = [ln for ln in r.stdout.splitlines() if "latest handshake" in ln]
            if peers:
                lines.append(f"Пиров online: {len(peers)}")
    return "\n".join(lines)


def format_awg_help() -> str:
    names = list_client_names()
    if not names:
        return (
            "AmneziaWG ещё не развёрнут.\n\n"
            "На сервере (ветка feature/amneziawg):\n"
            "<code>bash /root/setup-amnesiawg-lite.sh</code>\n\n"
            "После установки: /awg iphone"
        )
    return (
        "🛡 <b>AmneziaWG</b> — запасной UDP-протокол\n\n"
        "Команды:\n"
        "/awg iphone — конфиг для iPhone\n"
        "/awg router — конфиг для роутера\n"
        "/awg macbook — конфиг для Mac\n"
        "/awg status — статус сервиса\n\n"
        f"Доступно: {', '.join(names)}\n\n"
        "Клиент: AmneziaVPN или AmneziaWG (импорт .conf)"
    )
