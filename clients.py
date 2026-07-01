#!/usr/bin/env python3
"""Реестр VPN-клиентов: UUID на :443, лимиты, подписка."""
from __future__ import annotations

import base64
import json
import re
import secrets
import subprocess
from datetime import datetime
from pathlib import Path
from vpn_manager import (
    CLIENT_NAMES,
    PROFILE_ALIASES,
    SERVER_IP,
    XRAY_BIN,
    XRAY_CFG,
    _load_disabled_store,
    _profile_uuid,
    _run,
    _write_xray_config,
    build_vless_link,
    collect_link_entries,
    get_hysteria2_link,
    is_client_enabled,
    load_xray_config,
    main_443_inbound,
    regenerate_links_file,
)

DIR = Path(__file__).resolve().parent
CLIENTS_DB = DIR / "clients_db.json"
SUB_EXPORT_FILE = DIR / "vpn-profiles.txt"
SUB_DOMAIN = "287289.fornex.cloud"
LE_CERT = Path(f"/etc/letsencrypt/live/{SUB_DOMAIN}/fullchain.pem")
LE_KEY = Path(f"/etc/letsencrypt/live/{SUB_DOMAIN}/privkey.pem")
DEFAULT_SUB_PORT = 2097
DEFAULT_SUB_TLS_PORT = 2098
MAIN_PORT = 443
# Все VLESS-порты в одной подписке — клиент сам выберет рабочий
SUBSCRIPTION_PORTS = (443, 2053, 2096, 8448, 4433)
CLIENT_APP = "V2RayTun"


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")
    return s[:32] or "client"


def load_db() -> dict:
    if not CLIENTS_DB.is_file():
        return {"meta": {}, "clients": {}}
    try:
        return json.loads(CLIENTS_DB.read_text())
    except Exception:
        return {"meta": {}, "clients": {}}


def save_db(db: dict) -> None:
    CLIENTS_DB.parent.mkdir(parents=True, exist_ok=True)
    CLIENTS_DB.write_text(json.dumps(db, indent=2) + "\n")
    try:
        CLIENTS_DB.chmod(0o600)
    except Exception:
        pass


def ensure_meta(db: dict) -> dict:
    meta = db.setdefault("meta", {})
    if not meta.get("sub_token"):
        meta["sub_token"] = secrets.token_urlsafe(24)
    if not meta.get("sub_port"):
        meta["sub_port"] = DEFAULT_SUB_PORT
    if not meta.get("sub_tls_port"):
        meta["sub_tls_port"] = DEFAULT_SUB_TLS_PORT
    return meta


def sync_registry_from_xray() -> None:
    """Синхронизировать clients_db с конфигом Xray (:443)."""
    db = load_db()
    ensure_meta(db)
    clients = db.setdefault("clients", {})
    try:
        data = load_xray_config()
    except Exception:
        save_db(db)
        return

    for ib in data.get("inbounds", []):
        if ib.get("port") != MAIN_PORT or ib.get("protocol") != "vless":
            continue
        for c in (ib.get("settings") or {}).get("clients") or []:
            uid = c.get("id", "")
            if not uid:
                continue
            email = c.get("email", f"{uid[:8]}@xray.local")
            name = CLIENT_NAMES.get(uid) or email.split("@")[0]
            rec = clients.get(uid, {})
            rec.update({
                "name": rec.get("name") or name,
                "email": email,
                "port": MAIN_PORT,
                "enabled": True,
            })
            rec.setdefault("limit_gb", 0)
            rec.setdefault("created", datetime.now().strftime("%Y-%m-%d"))
            rec.setdefault("note", "")
            clients[uid] = rec
            PROFILE_ALIASES[name.lower()] = uid
            PROFILE_ALIASES[_slug(name)] = uid
        break

    save_db(db)


def _new_uuid() -> str:
    if XRAY_BIN.is_file():
        r = _run([str(XRAY_BIN), "uuid"], timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    import uuid as _uuid
    return str(_uuid.uuid4())


def list_active_uuids() -> list[str]:
    sync_registry_from_xray()
    db = load_db()
    disabled = _load_disabled_store()
    out: list[str] = []
    for uid, rec in db.get("clients", {}).items():
        if uid in disabled:
            continue
        if rec.get("enabled", True) and is_client_enabled(rec.get("name", "")):
            out.append(uid)
    if not out:
        try:
            ib = main_443_inbound()
            if ib:
                out = [c["id"] for c in ib["clients"]]
        except Exception:
            pass
    return out


def get_client_record(identifier: str) -> tuple[str, dict] | None:
    """По имени, slug или uuid."""
    sync_registry_from_xray()
    db = load_db()
    key = identifier.lower().strip()
    uid = PROFILE_ALIASES.get(key) or (_profile_uuid(key) if key else None)
    if uid and uid in db.get("clients", {}):
        return uid, db["clients"][uid]
    if re.fullmatch(r"[0-9a-f-]{36}", key) and key in db.get("clients", {}):
        return key, db["clients"][key]
    for u, rec in db.get("clients", {}).items():
        if rec.get("name", "").lower() == key or _slug(rec.get("name", "")) == key:
            return u, rec
    return None


def add_client(name: str, limit_gb: float = 0) -> str:
    sync_registry_from_xray()
    db = load_db()
    slug = _slug(name)
    for rec in db.get("clients", {}).values():
        if _slug(rec.get("name", "")) == slug:
            return f"❌ Клиент «{name}» уже есть (slug: {slug})"

    uid = _new_uuid()
    email = f"{slug}@xray.local"
    client_obj = {"id": uid, "flow": "xtls-rprx-vision", "email": email}

    data = load_xray_config()
    placed = False
    for ib in data.get("inbounds", []):
        if ib.get("port") == MAIN_PORT and ib.get("protocol") == "vless":
            clients = ib.setdefault("settings", {}).setdefault("clients", [])
            clients.append(client_obj)
            placed = True
            break
    if not placed:
        return "❌ Inbound :443 не найден"

    err = _write_xray_config(data)
    if err:
        return err

    db.setdefault("clients", {})[uid] = {
        "name": name,
        "email": email,
        "port": MAIN_PORT,
        "limit_gb": float(limit_gb or 0),
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "note": "",
        "enabled": True,
    }
    PROFILE_ALIASES[name.lower()] = uid
    PROFILE_ALIASES[slug] = uid
    CLIENT_NAMES[uid] = name
    save_db(db)
    regenerate_links_file()

    ib = main_443_inbound()
    if not ib:
        return f"✅ Клиент {name} добавлен, но ссылку не удалось построить"
    link = build_vless_link(uid, ib["port"], ib["sni"], ib["pub"], ib["sid"], name)
    lim = f"\nЛимит: {limit_gb} ГБ" if limit_gb else "\nЛимит: без ограничений"
    return (
        f"✅ Новый клиент: <b>{name}</b>\n"
        f"UUID: <code>{uid}</code>\n"
        f"Email: {email}{lim}\n\n"
        f"{link}"
    )


def set_client_limit(identifier: str, limit_gb: float) -> str:
    found = get_client_record(identifier)
    if not found:
        return f"Клиент «{identifier}» не найден"
    uid, rec = found
    db = load_db()
    rec["limit_gb"] = max(0.0, float(limit_gb))
    db["clients"][uid] = rec
    save_db(db)
    if rec["limit_gb"]:
        return f"✅ Лимит для {rec['name']}: {rec['limit_gb']} ГБ"
    return f"✅ Лимит для {rec['name']} снят (без ограничений)"


def revoke_client(identifier: str) -> str:
    """Полное удаление ключа (не временное отключение)."""
    found = get_client_record(identifier)
    if not found:
        return f"Клиент «{identifier}» не найден"
    uid, rec = found
    name = rec.get("name", identifier)

    data = load_xray_config()
    removed = False
    for ib in data.get("inbounds", []):
        if ib.get("port") != MAIN_PORT or ib.get("protocol") != "vless":
            continue
        clients = (ib.get("settings") or {}).get("clients") or []
        ib["settings"]["clients"] = [c for c in clients if c.get("id") != uid]
        removed = True
        break
    if not removed:
        return "❌ Inbound :443 не найден"

    err = _write_xray_config(data)
    if err:
        return err

    db = load_db()
    db.get("clients", {}).pop(uid, None)
    save_db(db)
    disabled = _load_disabled_store()
    if uid in disabled:
        del disabled[uid]
        from vpn_manager import _save_disabled_store
        _save_disabled_store(disabled)

    for k, v in list(PROFILE_ALIASES.items()):
        if v == uid:
            del PROFILE_ALIASES[k]
    CLIENT_NAMES.pop(uid, None)
    regenerate_links_file()
    return f"🗑 Клиент <b>{name}</b> удалён. Старый ключ больше не работает."


def connection_stats_today() -> dict[str, int]:
    from vpn_manager import ACCESS_LOG
    from collections import Counter

    today = datetime.now().strftime("%Y/%m/%d")
    cnt: Counter[str] = Counter()
    if not ACCESS_LOG.is_file():
        return {}
    try:
        for line in ACCESS_LOG.read_text(errors="ignore").splitlines()[-10000:]:
            if not line.startswith(today) or " accepted " not in line:
                continue
            m = re.search(r"email:\s*(\S+)", line)
            if m:
                cnt[m.group(1)] += 1
    except Exception:
        pass
    return dict(cnt)


def format_clients_registry() -> str:
    sync_registry_from_xray()
    db = load_db()
    stats = connection_stats_today()
    lines = ["📋 <b>Клиенты VLESS :443</b>", "Отдельный UUID = отдельный ключ", ""]

    if not db.get("clients"):
        lines.append("(пусто — /newclient Имя)")
        return "\n".join(lines)

    for uid, rec in db["clients"].items():
        name = rec.get("name", uid[:8])
        en = uid not in _load_disabled_store()
        try:
            data = load_xray_config()
            in_cfg = any(
                c.get("id") == uid
                for ib in data.get("inbounds", [])
                if ib.get("port") == MAIN_PORT
                for c in (ib.get("settings") or {}).get("clients") or []
            )
            en = en and in_cfg
        except Exception:
            pass
        icon = "🟢" if en else "🔴"
        email = rec.get("email", "")
        conn = stats.get(email, 0)
        lim = rec.get("limit_gb") or 0
        lim_s = f", лимит {lim} ГБ" if lim else ""
        lines.append(
            f"{icon} <b>{html_escape(name)}</b>\n"
            f"   <code>{uid[:18]}…</code>\n"
            f"   сегодня: {conn} подкл.{lim_s}"
        )
    lines.append("")
    lines.append("/newclient Имя — новый ключ")
    lines.append("/limit Имя 50 — лимит 50 ГБ")
    lines.append("/sub — подписочная ссылка")
    return "\n".join(lines)


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_subscription_lines(uuids: list[str] | None = None) -> list[str]:
    """Все ключи :443 + запасные порты + Hysteria2 в одной подписке."""
    sync_registry_from_xray()
    disabled = _load_disabled_store()
    want = set(uuids) if uuids else None
    out: list[str] = []
    seen: set[str] = set()

    def add(link: str) -> None:
        link = (link or "").strip()
        if not link or link in seen:
            return
        if link.startswith(("vless://", "hysteria2://")):
            out.append(link)
            seen.add(link)

    try:
        for entry in collect_link_entries():
            link = entry.get("link", "")
            port = int(entry.get("port") or 0)
            uid = entry.get("uuid", "")
            if port not in SUBSCRIPTION_PORTS and not link.startswith("hysteria2://"):
                continue
            if link.startswith("vless://"):
                if port == MAIN_PORT:
                    if uid in disabled:
                        continue
                    if want is not None and uid not in want:
                        continue
                add(link)
    except Exception:
        pass

    hy2 = get_hysteria2_link()
    if hy2:
        add(hy2)

    if not out:
        try:
            ib = main_443_inbound()
            if ib:
                for c in ib["clients"]:
                    uid = c["id"]
                    if uid in disabled:
                        continue
                    if want is not None and uid not in want:
                        continue
                    name = CLIENT_NAMES.get(uid) or c.get("name", "client")
                    add(build_vless_link(uid, ib["port"], ib["sni"], ib["pub"], ib["sid"], name))
        except Exception:
            for e in collect_link_entries():
                if e.get("port") in SUBSCRIPTION_PORTS:
                    add(e.get("link", ""))

    return out


def build_subscription_payload(uuids: list[str] | None = None) -> bytes:
    lines = build_subscription_lines(uuids)
    body = "\n".join(lines)
    return base64.b64encode(body.encode("utf-8"))


def export_subscription_txt(path: Path) -> int:
    """Текстовый файл со ссылками — для Happ без доверия к TLS."""
    lines = build_subscription_lines()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    try:
        path.chmod(0o600)
    except Exception:
        pass
    return len(lines)


def format_subscription_happ_help(n: int) -> str:
    return (
        f"📬 <b>Happ — импорт без сертификата</b>\n\n"
        f"В файле <b>{n}</b> профилей (VLESS + HY2).\n\n"
        "<b>Способ 1 — файл (рекомендуется):</b>\n"
        "1. Откройте файл <code>vpn-profiles.txt</code> ниже\n"
        "2. «Поделиться» → <b>Открыть в Happ</b>\n"
        "   (или скопируйте всё содержимое)\n"
        "3. Happ → <b>+</b> → из буфера обмена\n\n"
        "<b>Способ 2 — одна ссылка:</b>\n"
        "/links iphone → скопировать → Happ → + → вставить\n\n"
        "<i>URL подписки https://… не используйте в Happ — "
        "ругается на сертификат.</i>"
    )


def subscription_url() -> str:
    db = load_db()
    meta = ensure_meta(db)
    save_db(db)
    token = meta["sub_token"]
    tls_port = int(meta.get("sub_tls_port", DEFAULT_SUB_TLS_PORT))
    host = SUB_DOMAIN if LE_CERT.is_file() else SERVER_IP
    return f"https://{host}:{tls_port}/sub/{token}"


def subscription_stats() -> tuple[str, int]:
    sync_registry_from_xray()
    return subscription_url(), len(build_subscription_lines())


def format_subscription_help(n: int) -> str:
    ports = ", ".join(str(p) for p in SUBSCRIPTION_PORTS)
    has_le = LE_CERT.is_file()
    url_note = (
        "URL подписки с валидным сертификатом — во 2-м сообщении."
        if has_le
        else "URL https:// в Happ <b>не работает</b> (сертификат) — используйте файл ниже."
    )
    return (
        f"📬 <b>Подписка VPN</b> ({n} профилей)\n\n"
        f"VLESS: {ports} + Hysteria2 :8444\n\n"
        f"{url_note}\n\n"
        "<b>Happ:</b> откройте файл <code>vpn-profiles.txt</code>\n"
        "→ Поделиться → Открыть в Happ\n\n"
        "<b>V2RayTun:</b> URL из следующего сообщения\n"
        "или файл / импорт из буфера\n\n"
        "Одна ссылка: /links iphone"
    )


def list_manage_names() -> list[tuple[str, str]]:
    """(slug/name key, display label) для кнопок управления."""
    sync_registry_from_xray()
    db = load_db()
    out: list[tuple[str, str]] = []
    for uid, rec in db.get("clients", {}).items():
        name = rec.get("name", uid[:8])
        out.append((_slug(name), name))
    if not out:
        from vpn_manager import MANAGEABLE_PROFILES, MANAGE_LABELS
        for p in MANAGEABLE_PROFILES:
            out.append((p, MANAGE_LABELS.get(p, p)))
    return out
