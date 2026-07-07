#!/usr/bin/env python3
"""VPN/Xray helpers for Telegram bot."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

XRAY_CFG = Path("/usr/local/etc/xray/config.json")
XRAY_CFG_MIRROR = Path("/root/daily-telegram-security/xray-config.json")
XRAY_BIN = Path("/usr/local/bin/xray")
ACCESS_LOG = Path("/var/log/xray/access.log")
ERROR_LOG = Path("/var/log/xray/error.log")
HY2_LINK = Path("/root/hysteria2-link.txt")
VPN_LINKS = Path("/root/vpn-links.txt")
DISABLED_CLIENTS = Path("/root/daily-telegram-security/disabled_clients.json")
ALERT_STATE = Path("/root/daily-telegram-security/alert_state.json")
SNI_STATE = Path("/root/daily-telegram-security/sni_rotation.json")
HYSTERIA_CFG = Path("/etc/hysteria/config.yaml")
SERVER_IP = "103.75.126.216"

# Порядок авто-ротации SNI на :443 (без apple/google — чаще режут)
SNI_ROTATION_ORDER = ("cloudflare", "nginx", "github", "microsoft", "yahoo", "samsung")
SNI_ROTATION_DAYS = 14
DEFAULT_SNI_PRESET = "cloudflare"

MANAGEABLE_PROFILES = ("iphone", "router", "android", "client3", "client4")
MANAGE_LABELS = {
    "iphone": "📱 iPhone",
    "router": "🌐 Роутер",
    "android": "📲 Android",
    "client3": "👤 client3",
    "client4": "👤 client4",
}

SNI_PRESETS: dict[str, tuple[str, list[str]]] = {
    "cloudflare": ("www.cloudflare.com", ["www.cloudflare.com", "cloudflare.com"]),
    "nginx": ("nginx.org", ["nginx.org"]),
    "github": ("github.com", ["github.com", "www.github.com"]),
    "microsoft": ("www.microsoft.com", ["www.microsoft.com", "microsoft.com"]),
    "yahoo": ("www.yahoo.com", ["www.yahoo.com"]),
    "samsung": ("www.samsung.com", ["www.samsung.com"]),
    "google": ("www.google.com", ["www.google.com", "google.com"]),
    "apple": ("gateway.icloud.com", ["gateway.icloud.com"]),
}

CLIENT_NAMES: dict[str, str] = {
    "b34962b3-d2f8-4fe4-a53b-5f07c7939b68": "vpn-A",
    "ec92d5ee-c9f7-43c1-a78a-6f86bc2734eb": "vpn-B",
    "960136db-9a3d-4fdf-8a87-45ebda06b4c6": "client3",
    "23d309a0-9f86-4083-8ff7-fb2b7c608a2b": "client4",
    "b4ab5f27-fdc1-446c-a888-13f169d12b22": "iphone",
}

PROFILE_ALIASES: dict[str, str] = {
    "iphone": "b4ab5f27-fdc1-446c-a888-13f169d12b22",
    "айфон": "b4ab5f27-fdc1-446c-a888-13f169d12b22",
    "router": "b34962b3-d2f8-4fe4-a53b-5f07c7939b68",
    "роутер": "b34962b3-d2f8-4fe4-a53b-5f07c7939b68",
    "mac": "b34962b3-d2f8-4fe4-a53b-5f07c7939b68",
    "macbook": "b34962b3-d2f8-4fe4-a53b-5f07c7939b68",
    "vpn-a": "b34962b3-d2f8-4fe4-a53b-5f07c7939b68",
    "vpn-b": "ec92d5ee-c9f7-43c1-a78a-6f86bc2734eb",
    "android": "ec92d5ee-c9f7-43c1-a78a-6f86bc2734eb",
    "client3": "960136db-9a3d-4fdf-8a87-45ebda06b4c6",
    "client4": "23d309a0-9f86-4083-8ff7-fb2b7c608a2b",
}

# Запасные порты — отдельные inbound'ы (не путать с клиентами на :443).
PORT_ALIASES: dict[str, int] = {
    "2053": 2053,
    "wifi": 2053,
    "wifi-bypass": 2053,
    "2096": 2096,
    "samsung": 2096,
    "8448": 8448,
    "icloud": 8448,
    "apple-port": 8448,
    "4433": 4433,
    "yahoo": 4433,
}

CLIENT_LABELS: dict[str, str] = {
    "b4ab5f27-fdc1-446c-a888-13f169d12b22": "📱 iPhone (основной)",
    "b34962b3-d2f8-4fe4-a53b-5f07c7939b68": "🌐 Роутер / MacBook (vpn-A)",
    "ec92d5ee-c9f7-43c1-a78a-6f86bc2734eb": "📲 Android / vpn-B",
    "960136db-9a3d-4fdf-8a87-45ebda06b4c6": "👤 client3",
    "23d309a0-9f86-4083-8ff7-fb2b7c608a2b": "👤 client4",
    "b126874e-fb24-49ce-a360-c670ff2e16fb": "📡 Запасной :2053 (microsoft)",
    "2bef7b54-dad5-4191-a0d0-4f0efc30c651": "📡 Запасной :2096 (samsung)",
    "0077b3dd-69e0-4859-b475-7862b487050d": "📡 Запасной :8448 (apple)",
    "175719cf-d4e0-4d45-9a45-f64071487ca5": "📡 Запасной :4433 (yahoo)",
}


def _run(cmd: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _hysteria_cert_pin_sha256(cert: Path) -> str:
    """SHA256 pin сертификата HY2 (DER), для hysteria2:// ссылки."""
    import hashlib

    if not cert.is_file():
        return ""
    try:
        r = subprocess.run(
            ["openssl", "x509", "-in", str(cert), "-outform", "DER"],
            capture_output=True,
            timeout=10,
        )
        if r.returncode == 0 and r.stdout:
            return hashlib.sha256(r.stdout).hexdigest()
    except Exception:
        pass
    try:
        r2 = subprocess.run(
            "openssl x509 -in /etc/hysteria/server.crt -outform DER | openssl dgst -sha256",
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r2.returncode == 0:
            return (r2.stdout or "").split()[-1].strip()
    except Exception:
        pass
    return ""


def service_active(unit: str) -> str:
    try:
        r = _run(["systemctl", "is-active", unit], timeout=8)
        return (r.stdout or "").strip() or "unknown"
    except Exception:
        return "?"


def disk_usage() -> dict[str, str]:
    try:
        r = _run(["df", "-h", "/"], timeout=8)
        lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
        if len(lines) < 2:
            return {"raw": "n/a"}
        parts = lines[1].split()
        return {
            "size": parts[1],
            "used": parts[2],
            "avail": parts[3],
            "pct": parts[4],
        }
    except Exception:
        return {"raw": "n/a"}


def _sync_config_mirror() -> None:
    try:
        if XRAY_CFG.is_file():
            shutil.copy2(XRAY_CFG, XRAY_CFG_MIRROR)
            XRAY_CFG_MIRROR.chmod(0o600)
    except Exception:
        pass


def _config_test_path() -> Path:
    if XRAY_CFG.is_file():
        return XRAY_CFG
    if XRAY_CFG_MIRROR.is_file():
        return XRAY_CFG_MIRROR
    return XRAY_CFG


def _ensure_xray_config_file() -> bool:
    """Восстановить config.json из зеркала, если основной файл пропал."""
    if XRAY_CFG.is_file():
        return True
    if not XRAY_CFG_MIRROR.is_file():
        return False
    payload = XRAY_CFG_MIRROR.read_text()
    try:
        XRAY_CFG.write_text(payload)
        if XRAY_CFG.is_file():
            return True
    except (PermissionError, OSError):
        pass
    for cmd in (
        ["cp", str(XRAY_CFG_MIRROR), str(XRAY_CFG)],
        ["tee", str(XRAY_CFG)],
    ):
        try:
            if cmd[0] == "tee":
                r = subprocess.run(
                    cmd, input=payload, capture_output=True, text=True, timeout=15,
                )
            else:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and XRAY_CFG.is_file():
                return True
        except Exception:
            pass
    return XRAY_CFG.is_file()


def _save_xray_config_data(data: dict) -> tuple[str | None, str]:
    """
    Сохранить конфиг (зеркало + основной путь), тест, restart.
    Возвращает (ошибка или None, имя бэкапа).
    """
    payload = json.dumps(data, indent=2) + "\n"
    backup_name = ""

    if XRAY_CFG.is_file():
        backup = XRAY_CFG.with_name(f"config.json.bak.{datetime.now():%Y%m%d-%H%M%S}")
        try:
            shutil.copy2(XRAY_CFG, backup)
            backup_name = backup.name
        except Exception:
            pass
    elif XRAY_CFG_MIRROR.is_file():
        backup = XRAY_CFG.with_name(f"config.json.bak.{datetime.now():%Y%m%d-%H%M%S}")
        try:
            shutil.copy2(XRAY_CFG_MIRROR, backup)
            backup_name = backup.name
        except Exception:
            pass

    try:
        XRAY_CFG_MIRROR.parent.mkdir(parents=True, exist_ok=True)
        XRAY_CFG_MIRROR.write_text(payload)
        XRAY_CFG_MIRROR.chmod(0o600)
    except Exception as e:
        return f"Не удалось записать зеркало конфига: {e}", backup_name

    write_ok = False
    try:
        XRAY_CFG.write_text(payload)
        write_ok = True
    except (PermissionError, OSError):
        pass
    if not write_ok:
        r = subprocess.run(
            ["tee", str(XRAY_CFG)],
            input=payload, capture_output=True, text=True, timeout=15,
        )
        write_ok = r.returncode == 0 and XRAY_CFG.is_file()
    if not write_ok:
        r = subprocess.run(
            ["cp", str(XRAY_CFG_MIRROR), str(XRAY_CFG)],
            capture_output=True, text=True, timeout=15,
        )
        write_ok = r.returncode == 0 and XRAY_CFG.is_file()

    test_cfg = _config_test_path()
    if not test_cfg.is_file():
        return "config.json не найден (нет основного файла и зеркала)", backup_name

    test = _run([str(XRAY_BIN), "-test", "-config", str(test_cfg)], timeout=20)
    if test.returncode != 0:
        return f"❌ Тест конфига не прошёл.\n{(test.stderr or test.stdout)[:300]}", backup_name

    if not XRAY_CFG.is_file():
        _ensure_xray_config_file()

    restart = _run(["systemctl", "restart", "xray"], timeout=30)
    if restart.returncode != 0:
        return f"⚠️ Конфиг OK, но restart failed: {(restart.stderr or restart.stdout)[:200]}", backup_name

    try:
        _run(["chown", "nobody:nogroup", str(XRAY_CFG)], timeout=5)
        _run(["chmod", "644", str(XRAY_CFG)], timeout=5)
    except Exception:
        try:
            _run(["chmod", "644", str(XRAY_CFG)], timeout=5)
        except Exception:
            pass
    _sync_config_mirror()
    return None, backup_name


def load_xray_config() -> dict:
    last_err: Exception | None = None
    sources: list[Path | str] = []
    if XRAY_CFG_MIRROR.is_file():
        sources.append(XRAY_CFG_MIRROR)
    sources.append(XRAY_CFG)
    for src in sources:
        try:
            raw = Path(src).read_text() if isinstance(src, Path) else ""
            if raw:
                return json.loads(raw)
        except Exception as e:
            last_err = e
    try:
        r = _run(["cat", str(XRAY_CFG)], timeout=15)
        if r.returncode == 0 and r.stdout:
            return json.loads(r.stdout)
    except Exception as e:
        last_err = e
    raise RuntimeError(f"cannot read xray config: {last_err}")


def reality_public_key(private_key: str) -> str:
    if not XRAY_BIN.is_file():
        raise RuntimeError("xray binary not found")
    r = _run([str(XRAY_BIN), "x25519", "-i", private_key], timeout=15)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "x25519 failed").strip()[:200])
    for line in (r.stdout or "").splitlines():
        low = line.lower()
        if low.startswith("password"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("public key not in x25519 output")


def pick_short_id(short_ids: list[str]) -> str:
    for sid in short_ids:
        if sid:
            return sid
    return ""


def parse_inbound(ib: dict) -> dict | None:
    if ib.get("protocol") != "vless":
        return None
    port = ib.get("port")
    rs = (ib.get("streamSettings") or {}).get("realitySettings") or {}
    if not rs:
        return None
    priv = rs.get("privateKey", "")
    meta = ib.get("_meta") or {}
    pub = meta.get("pub") or (reality_public_key(priv) if priv else "")
    sid = meta.get("sid") or pick_short_id(rs.get("shortIds") or [])
    dest = (rs.get("dest") or "").split(":")[0]
    clients = []
    for c in (ib.get("settings") or {}).get("clients") or []:
        uid = c.get("id", "")
        email = c.get("email", "")
        name = CLIENT_NAMES.get(uid) or email.split("@")[0] or uid[:8]
        clients.append({"id": uid, "email": email, "name": name})
    return {
        "tag": ib.get("tag") or f"port-{port}",
        "port": port,
        "sni": dest,
        "pub": pub,
        "sid": sid,
        "clients": clients,
    }


def list_inbounds() -> list[dict]:
    data = load_xray_config()
    out = []
    for ib in data.get("inbounds", []):
        parsed = parse_inbound(ib)
        if parsed:
            out.append(parsed)
    return sorted(out, key=lambda x: x["port"] or 0)


def main_443_inbound() -> dict | None:
    for ib in list_inbounds():
        if ib["port"] == 443:
            return ib
    return None


def build_vless_link(
    uuid: str,
    port: int,
    sni: str,
    pub: str,
    sid: str,
    name: str,
    fp: str = "firefox",
) -> str:
    host = SERVER_IP
    q = (
        f"encryption=none&flow=xtls-rprx-vision&security=reality"
        f"&sni={quote(sni, safe='')}&fp={fp}&pbk={quote(pub, safe='')}"
        f"&sid={quote(sid, safe='')}&type=tcp"
    )
    return f"vless://{uuid}@{host}:{port}?{q}#{quote(name, safe='')}"


def _label_for(uuid: str, port: int, sni: str, name: str) -> str:
    base = CLIENT_LABELS.get(uuid) or name
    return f"{base} — порт {port}, SNI {sni}"


def _parse_links_file() -> list[dict]:
    """Fallback: vless/hysteria2 из /root/vpn-links.txt."""
    if not VPN_LINKS.is_file():
        return []
    entries: list[dict] = []
    for line in VPN_LINKS.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line.startswith(("vless://", "hysteria2://")):
            continue
        m = re.search(r"^(vless|hysteria2)://([^@]+)@[^:]+:(\d+)", line)
        uuid = m.group(2) if m else ""
        port = int(m.group(3)) if m else 0
        sni_m = re.search(r"[?&]sni=([^&]+)", line)
        sni = sni_m.group(1) if sni_m else ""
        name_m = re.search(r"#(.+)$", line)
        name = name_m.group(1) if name_m else "link"
        entries.append({
            "label": _label_for(uuid, port, sni, name) if uuid else f"🔗 {name}",
            "link": line,
            "uuid": uuid,
            "port": port,
            "sni": sni,
        })
    return entries


def collect_link_entries(fp: str = "firefox") -> list[dict]:
    entries: list[dict] = []
    try:
        for ib in list_inbounds():
            for c in ib["clients"]:
                uid = c["id"]
                link = build_vless_link(uid, ib["port"], ib["sni"], ib["pub"], ib["sid"], c["name"], fp)
                entries.append({
                    "label": _label_for(uid, ib["port"], ib["sni"], c["name"]),
                    "link": link,
                    "uuid": uid,
                    "port": ib["port"],
                    "sni": ib["sni"],
                    "profile": CLIENT_NAMES.get(uid, c["name"]).lower(),
                })
    except Exception:
        entries = _parse_links_file()

    hy2_added = False
    if HY2_LINK.is_file():
        for line in HY2_LINK.read_text(errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("hysteria2://"):
                entries.append({
                    "label": "⚡ Hysteria2 (запасной протокол, :8444)",
                    "link": line,
                    "uuid": "",
                    "port": 8444,
                    "sni": "",
                    "profile": "hy2",
                })
                hy2_added = True
                break
    if not hy2_added:
        try:
            hy2 = get_hysteria2_link()
        except Exception:
            hy2 = None
        if hy2:
            entries.append({
                "label": "⚡ Hysteria2 (запасной протокол, :8444)",
                "link": hy2,
                "uuid": "",
                "port": 8444,
                "sni": "",
                "profile": "hy2",
            })
            hy2_added = True
    if not hy2_added and VPN_LINKS.is_file():
        for e in _parse_links_file():
            if e["link"].startswith("hysteria2://"):
                e["label"] = "⚡ Hysteria2 (запасной протокол, :8444)"
                e["profile"] = "hy2"
                if not any(x["link"] == e["link"] for x in entries):
                    entries.append(e)
                break
    return entries


def _entries_for_profile(profile: str, fp: str = "firefox") -> list[dict]:
    key = profile.lower().strip()
    all_entries = collect_link_entries(fp)

    if key in ("all", "все", "list", "список"):
        return all_entries

    if key in ("hy2", "hysteria", "hysteria2"):
        hy2 = [e for e in all_entries if e["link"].startswith("hysteria2://")]
        return hy2 or [{"label": "HY2", "link": "Hysteria2 не настроен", "uuid": "", "port": 0, "sni": ""}]

    if key.isdigit():
        port = int(key)
        found = [e for e in all_entries if e["port"] == port]
        return found or [{"label": f"Порт {port}", "link": f"Порт {port} не найден", "uuid": "", "port": port, "sni": ""}]

    if key in PORT_ALIASES:
        port = PORT_ALIASES[key]
        found = [e for e in all_entries if e["port"] == port]
        return found or [{"label": f"Порт {port}", "link": f"Порт {port} не найден", "uuid": "", "port": port, "sni": ""}]

    uuid = PROFILE_ALIASES.get(key)
    if uuid:
        found = [e for e in all_entries if e["uuid"] == uuid]
        if found:
            return found

    if re.fullmatch(r"[0-9a-f-]{36}", key):
        found = [e for e in all_entries if e["uuid"] == key]
        if found:
            return found

    return [{
        "label": profile,
        "link": f"Профиль '{profile}' не найден.\nПопробуйте: iphone, router, vpn-b, 2053, hy2, all",
        "uuid": "",
        "port": 0,
        "sni": "",
    }]


def link_entries_for_profile(profile: str, fp: str = "firefox") -> list[dict]:
    """Список {label, link, ...} для отправки в боте отдельными сообщениями."""
    return _entries_for_profile(profile, fp)


def format_links_message(profile: str, fp: str = "firefox") -> str:
    entries = _entries_for_profile(profile, fp)
    blocks: list[str] = []
    for e in entries:
        if e["uuid"]:
            blocks.append(f"{e['label']}\nUUID: {e['uuid']}\n{e['link']}")
        else:
            blocks.append(f"{e['label']}\n{e['link']}")
    return "\n\n".join(blocks)


def links_for_profile(profile: str, fp: str = "firefox") -> list[str]:
    return [e["link"] for e in _entries_for_profile(profile, fp) if e["link"].startswith(("vless://", "hysteria2://"))]


def format_vpn_status() -> str:
    xray = service_active("xray")
    hy2 = service_active("hysteria-server") if _run(["systemctl", "list-unit-files", "hysteria-server.service"], timeout=8).returncode == 0 else "n/a"
    disk = disk_usage()
    ib443 = main_443_inbound()

    lines = [
        "🖥 VPN / Xray",
        f"Xray: {xray}",
        f"Hysteria2: {hy2}",
    ]
    if disk.get("pct"):
        lines.append(f"Диск: {disk['used']} / {disk['size']} ({disk['pct']}, свободно {disk.get('avail', '?')})")
    else:
        lines.append(f"Диск: {disk.get('raw', 'n/a')}")

    if ib443:
        lines.append(f"SNI :443 → {ib443['sni']}")
        lines.append(f"Клиентов на 443: {len(ib443['clients'])}")

    ports = []
    try:
        r = _run(["ss", "-tlnp"], timeout=10)
        for ib in list_inbounds():
            p = ib["port"]
            if p and f":{p} " in (r.stdout or ""):
                ports.append(str(p))
    except Exception:
        ports = [str(ib["port"]) for ib in list_inbounds()]

    if ports:
        lines.append("Порты VLESS: " + ", ".join(sorted(ports, key=int)))

    today = datetime.now().strftime("%Y/%m/%d")
    accepted = 0
    emails: Counter[str] = Counter()
    if ACCESS_LOG.is_file():
        try:
            tail = ACCESS_LOG.read_text(errors="ignore").splitlines()[-5000:]
            for line in tail:
                if not line.startswith(today) or " accepted " not in line:
                    continue
                accepted += 1
                m = re.search(r"email:\s*(\S+)", line)
                if m:
                    emails[m.group(1)] += 1
        except Exception:
            pass
    lines.append(f"Подключений сегодня (accepted): {accepted}")
    if emails:
        top = emails.most_common(3)
        lines.append("Активные: " + ", ".join(f"{e.split('@')[0]}({n})" for e, n in top))

    err_tail = ""
    if ERROR_LOG.is_file():
        try:
            elines = [l for l in ERROR_LOG.read_text(errors="ignore").splitlines() if l.strip()]
            if elines:
                err_tail = elines[-1][-100:]
        except Exception:
            pass
    if err_tail:
        lines.append(f"error.log: …{err_tail}")

    lines.append("")
    lines.append("Команды: /links iphone · /sni · /logs · /disk")
    return "\n".join(lines)


def get_hysteria2_link() -> str | None:
    """Актуальная ссылка HY2 из файла или /etc/hysteria/config.yaml."""
    if HY2_LINK.is_file():
        for line in HY2_LINK.read_text(errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("hysteria2://"):
                return line
    if not HYSTERIA_CFG.is_file():
        return None
    try:
        import yaml  # type: ignore
        cfg = yaml.safe_load(HYSTERIA_CFG.read_text())
    except Exception:
        cfg = None
    if not cfg:
        # простой парсер без PyYAML
        text = HYSTERIA_CFG.read_text(errors="ignore")
        pwd_m = re.search(r"password:\s*(\S+)", text)
        port_m = re.search(r"listen:\s*:?(\d+)", text)
        if not pwd_m:
            return None
        port = port_m.group(1) if port_m else "8444"
        pwd = pwd_m.group(1)
    else:
        pwd = (cfg.get("auth") or {}).get("password", "")
        listen = str(cfg.get("listen", ":8444"))
        port = re.sub(r"\D", "", listen) or "8444"
    if not pwd:
        return None
    pin = _hysteria_cert_pin_sha256(Path("/etc/hysteria/server.crt"))
    sni = "www.cloudflare.com"
    q = f"pinSHA256={pin}&sni={quote(sni, safe='')}" if pin else f"sni={quote(sni, safe='')}"
    return f"hysteria2://{pwd}@{SERVER_IP}:{port}/?{q}#Fornex-HY2"


def _load_sni_state() -> dict:
    if not SNI_STATE.is_file():
        return {
            "enabled": False,
            "interval_days": SNI_ROTATION_DAYS,
            "current_preset": DEFAULT_SNI_PRESET,
            "last_rotation": None,
        }
    try:
        data = json.loads(SNI_STATE.read_text())
    except Exception:
        data = {}
    data.setdefault("enabled", False)
    data.setdefault("interval_days", SNI_ROTATION_DAYS)
    data.setdefault("current_preset", DEFAULT_SNI_PRESET)
    return data


def _save_sni_state(data: dict) -> None:
    SNI_STATE.parent.mkdir(parents=True, exist_ok=True)
    SNI_STATE.write_text(json.dumps(data, indent=2) + "\n")
    try:
        SNI_STATE.chmod(0o600)
    except Exception:
        pass


def format_sni_rotation_info() -> str:
    st = _load_sni_state()
    ib = main_443_inbound()
    current = ib["sni"] if ib else "?"
    nxt = "—"
    cur_preset = st.get("current_preset", DEFAULT_SNI_PRESET)
    if cur_preset in SNI_ROTATION_ORDER:
        idx = SNI_ROTATION_ORDER.index(cur_preset)
        nxt = SNI_PRESETS[SNI_ROTATION_ORDER[(idx + 1) % len(SNI_ROTATION_ORDER)]][0]
    last = st.get("last_rotation") or "ещё не было"
    days = st.get("interval_days", SNI_ROTATION_DAYS)
    en = "вкл" if st.get("enabled", True) else "выкл"
    return (
        f"🔄 <b>Авто-ротация SNI</b>: {en}\n"
        f"Сейчас :443 → <code>{current}</code>\n"
        f"Следующая маска: {nxt}\n"
        f"Интервал: каждые {days} дн.\n"
        f"Последняя смена: {last}\n\n"
        f"После смены обновите подписку в V2RayTun (/sub)"
    )


def poll_sni_rotation() -> list[str]:
    """Плановая смена SNI. Возвращает сообщения для Telegram."""
    # Авто-ротация отключена (sni_rotation.json enabled=false + закомментирован вызов в bot_poller).
    # Для восстановления: enabled=true, раскомментировать блок в bot_poller.py main loop.
    state = _load_sni_state()
    if not state.get("enabled", False):
        return []

    days = int(state.get("interval_days", SNI_ROTATION_DAYS))
    last = state.get("last_rotation")
    if last:
        try:
            last_dt = datetime.fromisoformat(str(last))
            if (datetime.now() - last_dt).days < days:
                return []
        except ValueError:
            pass

    cur = state.get("current_preset", DEFAULT_SNI_PRESET)
    if cur not in SNI_ROTATION_ORDER:
        cur = DEFAULT_SNI_PRESET
    idx = SNI_ROTATION_ORDER.index(cur)
    next_preset = SNI_ROTATION_ORDER[(idx + 1) % len(SNI_ROTATION_ORDER)]

    result = change_main_sni(next_preset, notify=False)
    if not result.startswith("✅"):
        return [f"⚠️ Авто-ротация SNI не удалась:\n{result[:400]}"]

    state["current_preset"] = next_preset
    state["last_rotation"] = datetime.now().isoformat(timespec="seconds")
    _save_sni_state(state)

    host = SNI_PRESETS[next_preset][0]
    return [
        "🔄 <b>Авто-смена SNI на :443</b>\n\n"
        f"Новая маска: <b>{host}</b>\n\n"
        "📬 <b>Обновите подписку в V2RayTun:</b>\n"
        "Подписки → удержать → Обновить\n"
        "или /sub — скопировать URL заново.\n\n"
        "<i>Старые ссылки на :443 без обновления перестанут работать.</i>"
    ]


def format_sni_info() -> str:
    ib = main_443_inbound()
    current = ib["sni"] if ib else "?"
    presets = ", ".join(SNI_ROTATION_ORDER)
    rot = format_sni_rotation_info()
    return (
        f"Текущий SNI (порт 443): {current}\n\n"
        f"Сменить вручную: /sni <имя>\n"
        f"Рекомендуемые: {presets}\n\n"
        f"После смены обновите подписку: /sub\n\n"
        f"{rot}"
    )


def change_main_sni(preset: str, notify: bool = True) -> str:
    key = preset.lower().strip()
    if key not in SNI_PRESETS:
        return f"Неизвестный SNI '{preset}'. Доступно: {', '.join(sorted(SNI_PRESETS))}"

    dest_host, server_names = SNI_PRESETS[key]
    _ensure_xray_config_file()
    if not XRAY_CFG.is_file() and not XRAY_CFG_MIRROR.is_file():
        return "config.json не найден"

    try:
        data = load_xray_config()
    except Exception as e:
        return f"config.json не читается: {e}"

    found = False
    for ib in data.get("inbounds", []):
        if ib.get("port") == 443 and ib.get("protocol") == "vless":
            rs = ib["streamSettings"]["realitySettings"]
            rs["dest"] = f"{dest_host}:443"
            rs["serverNames"] = server_names
            found = True
            break
    if not found:
        return "Inbound :443 не найден в конфиге"

    err, backup_name = _save_xray_config_data(data)
    if err:
        return err

    regenerate_links_file()
    st = _load_sni_state()
    st["current_preset"] = key
    if notify:
        st["last_rotation"] = datetime.now().isoformat(timespec="seconds")
    _save_sni_state(st)
    iphone = links_for_profile("iphone")[0]
    bak = f"Бэкап: {backup_name}\n\n" if backup_name else ""
    return (
        f"✅ SNI на :443 → {dest_host}\n"
        f"{bak}"
        f"📬 Обновите подписку в V2RayTun (/sub)\n\n"
        f"iPhone:\n{iphone}"
    )


def regenerate_links_file() -> None:
    lines = [
        "============================================",
        "  VPN Fornex — актуальные ссылки (бот)",
        f"  Обновлено: {datetime.now():%Y-%m-%d %H:%M}",
        "============================================",
        "",
    ]
    for link in links_for_profile("all"):
        lines.append(link)
        lines.append("")
    VPN_LINKS.write_text("\n".join(lines))


def tail_access_log(n: int = 15) -> str:
    n = max(5, min(40, n))
    if not ACCESS_LOG.is_file():
        return "access.log не найден"
    try:
        lines = ACCESS_LOG.read_text(errors="ignore").splitlines()
        chunk = lines[-n:]
        return "\n".join(l[-120:] for l in chunk) or "(пусто)"
    except Exception as e:
        return f"Ошибка чтения лога: {e}"


def restart_service(name: str) -> str:
    allowed = {"xray": "xray", "hy2": "hysteria-server", "hysteria": "hysteria-server"}
    unit = allowed.get(name.lower())
    if not unit:
        return "Доступно: /restart xray или /restart hy2"
    r = _run(["systemctl", "restart", unit], timeout=45)
    if r.returncode != 0:
        return f"❌ restart {unit}: {(r.stderr or r.stdout)[:200]}"
    st = service_active(unit)
    return f"✅ {unit} перезапущен ({st})"


def format_disk_report() -> str:
    d = disk_usage()
    lines = ["💾 Диск /"]
    if d.get("pct"):
        lines.append(f"{d['used']} / {d['size']} ({d['pct']}), свободно {d['avail']}")
        try:
            pct_num = int(str(d["pct"]).rstrip("%"))
            if pct_num >= 90:
                lines.append("🔴 Критично! Запустите очистку.")
            elif pct_num >= 80:
                lines.append("🟡 Мало места — скоро понадобится очистка.")
        except ValueError:
            pass
    else:
        lines.append(str(d.get("raw", "n/a")))
    lines.append("")
    lines.append("Безопасная очистка: кнопка в меню или /cleanup")
    return "\n".join(lines)


def format_uptime() -> str:
    try:
        r = _run(["uptime", "-p"], timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        return _read_first_line("/proc/uptime")
    except Exception:
        return "n/a"


def _read_first_line(path: str) -> str:
    try:
        return Path(path).read_text(errors="ignore").splitlines()[0][:80]
    except Exception:
        return "n/a"


def format_clients_report() -> str:
    today = datetime.now().strftime("%Y/%m/%d")
    emails: Counter[str] = Counter()
    last_seen: dict[str, str] = {}
    if not ACCESS_LOG.is_file():
        return "access.log недоступен"
    try:
        for line in ACCESS_LOG.read_text(errors="ignore").splitlines()[-8000:]:
            if not line.startswith(today) or " accepted " not in line:
                continue
            m = re.search(r"email:\s*(\S+)", line)
            if not m:
                continue
            email = m.group(1)
            emails[email] += 1
            ts = line[11:19] if len(line) > 19 else "?"
            last_seen[email] = ts
    except Exception as e:
        return f"Ошибка: {e}"

    if not emails:
        return f"Сегодня ({today}) подключений не было."

    lines = [f"👥 Клиенты сегодня ({today})", ""]
    for email, cnt in emails.most_common():
        short = email.split("@")[0]
        lines.append(f"• {short}: {cnt} подкл., последний {last_seen.get(email, '?')}")
    return "\n".join(lines)


def format_health_check() -> str:
    lines = ["🩺 Проверка сервера", ""]
    xray = service_active("xray")
    ssh = service_active("ssh")
    lines.append(f"Xray: {'✅' if xray == 'active' else '❌'} {xray}")
    lines.append(f"SSH:  {'✅' if ssh == 'active' else '❌'} {ssh}")

    ib443 = main_443_inbound()
    if ib443:
        lines.append(f"SNI :443 → {ib443['sni']}")

    try:
        r = _run(["ss", "-tlnp"], timeout=8)
        out = r.stdout or ""
        for port in (443, 2053, 8443, 8444):
            ok = f":{port} " in out
            lines.append(f"Порт {port}: {'✅ слушает' if ok else '❌ нет'}")
    except Exception:
        lines.append("Порты: не удалось проверить")

    try:
        r = _run(
            [
                "curl", "-sS", "--noproxy", "*", "-o", "/dev/null",
                "-w", "%{http_code}", "--connect-timeout", "5", "--max-time", "8",
                "https://www.microsoft.com",
            ],
            timeout=12,
        )
        code = (r.stdout or "").strip()
        lines.append(f"Интернет (curl microsoft): {'✅ HTTP ' + code if code.startswith('2') or code == '301' else '❌ ' + code}")
    except Exception:
        lines.append("Интернет: ❌ таймаут")

    d = disk_usage()
    if d.get("pct"):
        icon = "🔴" if int(str(d["pct"]).rstrip("%")) >= 90 else "🟡" if int(str(d["pct"]).rstrip("%")) >= 80 else "🟢"
        lines.append(f"Диск: {icon} {d['used']}/{d['size']} ({d['pct']})")

    lines.append(f"Uptime: {format_uptime()}")
    return "\n".join(lines)


def run_safe_cleanup() -> str:
    script = Path("/root/cleanup-safe.sh")
    if not script.is_file():
        return "Скрипт /root/cleanup-safe.sh не найден"
    before = disk_usage()
    try:
        r = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        out = (r.stdout or "")[-600:]
        after = disk_usage()
        msg = ["🧹 Очистка завершена", ""]
        if before.get("pct") and after.get("pct"):
            msg.append(f"Диск: {before['pct']} → {after['pct']} (свободно {after.get('avail', '?')})")
        if out.strip():
            msg.append(out.strip()[-400:])
        if r.returncode != 0:
            msg.append(f"⚠️ Код выхода: {r.returncode}")
        return "\n".join(msg)
    except subprocess.TimeoutExpired:
        return "❌ Очистка: таймаут"
    except Exception as e:
        return f"❌ Очистка: {e}"


def format_dashboard() -> str:
    """Краткая сводка «всё ли ок» — для /start и кнопки Статус."""
    health = format_health_check().splitlines()
    clients = format_clients_report().splitlines()
    pick = health[:8] + [""] + clients[:6]
    pick.append("")
    pick.append("Подробнее: кнопки меню ниже 👇")
    return "\n".join(pick)


def _write_xray_config(data: dict) -> str | None:
    """Сохранить конфиг, тест, restart. None = OK, иначе текст ошибки."""
    err, _ = _save_xray_config_data(data)
    return err


def _load_disabled_store() -> dict:
    if not DISABLED_CLIENTS.is_file():
        return {}
    try:
        return json.loads(DISABLED_CLIENTS.read_text())
    except Exception:
        return {}


def _save_disabled_store(data: dict) -> None:
    DISABLED_CLIENTS.parent.mkdir(parents=True, exist_ok=True)
    DISABLED_CLIENTS.write_text(json.dumps(data, indent=2) + "\n")


def _profile_uuid(profile: str) -> str | None:
    return PROFILE_ALIASES.get(profile.lower().strip())


def _client_email(uuid: str) -> str | None:
    try:
        data = load_xray_config()
        for ib in data.get("inbounds", []):
            if ib.get("protocol") != "vless":
                continue
            for c in (ib.get("settings") or {}).get("clients") or []:
                if c.get("id") == uuid:
                    return c.get("email")
        disabled = _load_disabled_store()
        if uuid in disabled:
            return disabled[uuid].get("client", {}).get("email")
    except Exception:
        pass
    return None


def is_client_enabled(profile: str) -> bool:
    uuid = _profile_uuid(profile)
    if not uuid:
        return False
    if uuid in _load_disabled_store():
        return False
    try:
        data = load_xray_config()
        for ib in data.get("inbounds", []):
            for c in (ib.get("settings") or {}).get("clients") or []:
                if c.get("id") == uuid:
                    return True
        return False
    except Exception:
        return True


def format_manage_clients() -> str:
    lines = ["🔧 Управление клиентами (порт 443)", ""]
    for p in MANAGEABLE_PROFILES:
        en = is_client_enabled(p)
        lines.append(f"{'🟢 вкл' if en else '🔴 выкл'} — {MANAGE_LABELS.get(p, p)}")
    lines.append("")
    lines.append("Выключенный клиент не сможет подключиться.")
    return "\n".join(lines)


def set_client_enabled(profile: str, enable: bool) -> str:
    uuid = _profile_uuid(profile)
    if not uuid:
        return f"Неизвестный профиль: {profile}"

    label = MANAGE_LABELS.get(profile, profile)
    disabled = _load_disabled_store()

    if enable:
        if uuid not in disabled:
            if is_client_enabled(profile):
                return f"ℹ️ {label} уже включён"
            return f"❌ Нет сохранённых данных для включения {label}"
        rec = disabled.pop(uuid)
        data = load_xray_config()
        port = rec.get("port", 443)
        client = rec.get("client")
        if not client:
            return "❌ Битая запись в disabled_clients.json"
        placed = False
        for ib in data.get("inbounds", []):
            if ib.get("port") == port and ib.get("protocol") == "vless":
                clients = ib.setdefault("settings", {}).setdefault("clients", [])
                if not any(c.get("id") == uuid for c in clients):
                    clients.append(client)
                placed = True
                break
        if not placed:
            disabled[uuid] = rec
            _save_disabled_store(disabled)
            return f"❌ Inbound :{port} не найден"
        _save_disabled_store(disabled)
        err = _write_xray_config(data)
        if err:
            return err
        regenerate_links_file()
        return f"✅ {label} включён"

    if not is_client_enabled(profile):
        return f"ℹ️ {label} уже выключен"

    data = load_xray_config()
    removed = None
    port = 443
    for ib in data.get("inbounds", []):
        if ib.get("protocol") != "vless":
            continue
        clients = (ib.get("settings") or {}).get("clients") or []
        for i, c in enumerate(clients):
            if c.get("id") == uuid:
                removed = clients.pop(i)
                port = ib.get("port", 443)
                break
        if removed:
            break
    if not removed:
        return f"❌ Клиент {label} не найден в конфиге"

    disabled[uuid] = {
        "client": removed,
        "port": port,
        "profile": profile,
        "disabled_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_disabled_store(disabled)
    err = _write_xray_config(data)
    if err:
        return err
    return f"🔴 {label} выключен — подключение невозможно"


def generate_qr_png(link: str, out: Path) -> str | None:
    """None = OK, иначе ошибка."""
    out.parent.mkdir(parents=True, exist_ok=True)
    r = _run(
        ["qrencode", "-o", str(out), "-s", "10", "-m", "2", "--foreground=000000", "--background=FFFFFF", link],
        timeout=20,
    )
    if r.returncode != 0 or not out.is_file():
        return (r.stderr or "qrencode failed")[:200]
    return None


def _access_log_pos() -> int:
    if not ACCESS_LOG.is_file():
        return 0
    try:
        return ACCESS_LOG.stat().st_size
    except Exception:
        return 0


def _find_new_accept(email: str, since_pos: int) -> str | None:
    if not ACCESS_LOG.is_file() or not email:
        return None
    try:
        with ACCESS_LOG.open("rb") as f:
            f.seek(since_pos)
            chunk = f.read().decode(errors="ignore")
        for line in reversed(chunk.splitlines()):
            if " accepted " in line and email in line:
                return line[-140:]
    except Exception:
        pass
    return None


def run_vpn_connect_test(profile: str, wait_sec: int = 50) -> str:
    import time as _time

    uuid = _profile_uuid(profile)
    if not uuid:
        return f"Неизвестный профиль: {profile}"
    email = _client_email(uuid)
    if not email:
        return f"Не найден email для {profile}"

    if not is_client_enabled(profile):
        return f"🔴 Клиент {MANAGE_LABELS.get(profile, profile)} выключен — сначала включите."

    label = MANAGE_LABELS.get(profile, profile)
    since = _access_log_pos()
    lines = [
        f"🔌 Тест VPN — {label}",
        f"Ожидаю email: {email}",
        "",
        "1. Включите VPN на устройстве СЕЙЧАС",
        f"2. Жду {wait_sec} сек…",
    ]

    found: str | None = None
    steps = max(1, wait_sec // 5)
    for _ in range(steps):
        _time.sleep(5)
        hit = _find_new_accept(email, since)
        if hit:
            found = hit
            break

    if found:
        lines.append("")
        lines.append("✅ Подключение видно в логе!")
        lines.append(found)
    else:
        lines.append("")
        lines.append("❌ За время ожидания подключений не было.")
        lines.append("Проверьте: ссылка, SNI, Wi‑Fi/LTE, V2RayTun.")
    return "\n".join(lines)


def _disk_pct_num() -> int | None:
    d = disk_usage()
    if not d.get("pct"):
        return None
    try:
        return int(str(d["pct"]).rstrip("%"))
    except ValueError:
        return None


def _load_alert_state() -> dict:
    if not ALERT_STATE.is_file():
        return {}
    try:
        return json.loads(ALERT_STATE.read_text())
    except Exception:
        return {}


def _save_alert_state(state: dict) -> None:
    ALERT_STATE.parent.mkdir(parents=True, exist_ok=True)
    ALERT_STATE.write_text(json.dumps(state, indent=2) + "\n")


def poll_alerts() -> list[str]:
    """
    Проверка алертов с антиспамом. Возвращает список сообщений для отправки.
    """
    state = _load_alert_state()
    out: list[str] = []
    now = datetime.now().isoformat(timespec="seconds")

    xray = service_active("xray")
    if xray != "active":
        if not state.get("xray_down"):
            out.append("🚨 <b>ALERT</b>: Xray не работает!\nПроверьте: /check или /restart xray")
            state["xray_down"] = now
    elif state.get("xray_down"):
        out.append("✅ Xray снова работает")
        del state["xray_down"]

    pct = _disk_pct_num()
    if pct is not None:
        if pct >= 90:
            if not state.get("disk_crit"):
                out.append(f"🚨 <b>ALERT</b>: диск заполнен на {pct}%!\n/cleanup — очистка")
                state["disk_crit"] = now
        elif state.get("disk_crit"):
            del state["disk_crit"]

        if pct >= 80:
            if not state.get("disk_warn"):
                out.append(f"⚠️ Диск {pct}% — скоро понадобится очистка (/cleanup)")
                state["disk_warn"] = now
        elif pct < 75 and state.get("disk_warn"):
            del state["disk_warn"]

    _save_alert_state(state)
    return out
