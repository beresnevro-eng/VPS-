#!/usr/bin/env python3
"""
Telegram-бот @My_mini_server_bot — управление VPN и сервером с телефона.
Админ: полный доступ. Гости (limited_users.json): ссылки и /newclient.
"""
from __future__ import annotations

import html
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import access_control as ac

for _pk in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_pk, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

DIR = Path(__file__).resolve().parent
CONFIG = DIR / "config.env"
CHAT_FILE = DIR / "tg_chat_id"
OFFSET_FILE = DIR / ".last_update_id"

WELCOME = (
    "<b>Fornex VPN — пульт v5</b>\n\n"
    "Ссылки приходят <b>отдельным сообщением</b> — удобно копировать.\n\n"
    "Клиент: <b>V2RayTun</b> / Happ\n"
    "📬 Подписка: /sub\n"
    "Один ключ: /links iphone\n\n"
    "Главное меню: /menu"
)

HELP_TEXT = """<b>Команды</b>

<b>Быстрые</b>
/menu — кнопочное меню
/check — диагностика
/vpn — статус VPN
/test iphone — тест подключения (50 сек)
/qr iphone — QR-код ссылки

<b>Ссылки</b>
/links — все
/links iphone / router / 2053 / hy2

<b>Клиенты (как в 3x-ui)</b>
/users — все UUID на :443
/newclient Имя — новый ключ
/limit Имя 50 — лимит 50 ГБ (учёт)
/revoke Имя — удалить ключ навсегда
/sub — подписочная ссылка (все ключи)
/manage — вкл/выкл доступ
/clients — активность сегодня

<b>Настройка</b>
/sni microsoft — сменить маскировку
/restart xray — перезапуск
/cleanup — очистка диска
/backup — создать и скачать бэкап (только админ)

<b>Сервер</b>
/status — безопасность
/report — PNG-отчёт
/disk / /logs

<b>Алерты</b> — бот сам пишет, если Xray упал или диск &gt;80%
<b>SNI</b> — авто-смена каждые 14 дн. + уведомление (/sni)

<b>V2RayTun</b>
/sub — подписка (443, 2053, 2096… + HY2)
/links iphone — один ключ
/qr iphone — QR для импорта

<i>Кнопки: /menu · слова: меню, статус, ссылки</i>"""

WELCOME_LIMITED = (
    "<b>VPN — получение ссылок</b>\n\n"
    "Доступно:\n"
    "• /links — ссылка на подключение\n"
    "• /newclient Имя — новый ключ\n"
    "• /qr iphone — QR-код\n\n"
    "Меню: /menu"
)

HELP_LIMITED = """<b>Команды</b>

/links — все ссылки
/links iphone — одна ссылка
/newclient Имя — создать новый ключ
/qr iphone — QR для импорта
/menu — кнопочное меню

<i>Управление сервером — только у администратора.</i>"""

DENY_ADMIN_ONLY = "⛔ Эта функция только для администратора."

# --- Inline keyboards (callback_data ≤ 64 байт) ---

def _btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data[:64]}


def kb_main() -> dict:
    return {"inline_keyboard": [
        [_btn("📊 Сводка", "m:dash"), _btn("🩺 Проверка", "m:check")],
        [_btn("🔗 Ссылки", "m:links"), _btn("📷 QR iPhone", "q:iphone")],
        [_btn("🔌 Тест VPN", "m:test"), _btn("📬 Подписка", "m:sub")],
        [_btn("👤 Клиенты", "m:users"), _btn("🔧 Вкл/Выкл", "m:manage")],
        [_btn("🎭 SNI", "m:sni"), _btn("📊 Активность", "m:clients")],
        [_btn("💾 Диск", "m:disk"), _btn("📦 Бэкап", "m:backup")],
        [_btn("🔄 Перезапуск", "m:restart")],
        [_btn("📝 Логи", "m:logs"), _btn("🔐 Отчёт", "m:report")],
        [_btn("❓ Справка", "m:help")],
    ]}


def kb_main_limited() -> dict:
    return {"inline_keyboard": [
        [_btn("🔗 Ссылки", "m:links"), _btn("📷 QR iPhone", "q:iphone")],
        [_btn("➕ Новый ключ", "m:newclient")],
        [_btn("❓ Справка", "m:help")],
    ]}


def kb_links_limited() -> dict:
    return {"inline_keyboard": [
        [_btn("📱 iPhone", "l:iphone"), _btn("🌐 Роутер", "l:router")],
        [_btn("📲 Android", "l:android"), _btn("⚡ HY2", "l:hy2")],
        [_btn("📡 :2053", "l:2053")],
        [_btn("◀️ Меню", "m:main")],
    ]}


def _kb_for(role: str) -> dict:
    return kb_main_limited() if role == "limited" else kb_main()


def _kb_links_for(role: str) -> dict:
    return kb_links_limited() if role == "limited" else kb_links()


def kb_test() -> dict:
    return {"inline_keyboard": [
        [_btn("📱 iPhone", "t:iphone"), _btn("🌐 Роутер", "t:router")],
        [_btn("📲 Android", "t:android")],
        [_btn("◀️ Главное меню", "m:main")],
    ]}


def kb_manage(vpn, clients_mod) -> dict:
    rows = []
    for key, label in clients_mod.list_manage_names():
        en = vpn.is_client_enabled(key)
        icon = "🟢" if en else "🔴"
        action = "off" if en else "on"
        short = label.split(maxsplit=1)[-1][:20]
        rows.append([_btn(f"{icon} {short}", f"u:ask:{action}:{key}")])
    rows.append([_btn("◀️ Главное меню", "m:main")])
    return {"inline_keyboard": rows}


def kb_qr() -> dict:
    return {"inline_keyboard": [
        [_btn("📱 iPhone", "q:iphone"), _btn("🌐 Роутер", "q:router")],
        [_btn("📲 Android", "q:android"), _btn("⚡ HY2", "q:hy2")],
        [_btn("◀️ Главное меню", "m:main")],
    ]}


def kb_sub() -> dict:
    return {"inline_keyboard": [
        [_btn("📋 Ещё раз ссылку", "sub:url")],
        [_btn("◀️ Главное меню", "m:main")],
    ]}


def send_document(token: str, chat_id: int, path: Path, caption: str = "") -> None:
    env = os.environ.copy()
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(k, None)
    r = subprocess.run(
        [
            "curl", "-sS", "--noproxy", "*", "-w", "\n%{http_code}",
            "-X", "POST", f"https://api.telegram.org/bot{token}/sendDocument",
            "-F", f"chat_id={chat_id}",
            "-F", f"document=@{path}",
            "-F", f"caption={caption}",
        ],
        capture_output=True, text=True, env=env, timeout=120,
    )
    out = r.stdout or ""
    parts = out.rsplit("\n", 1)
    body = parts[0] if len(parts) == 2 else out
    http = parts[1] if len(parts) == 2 else ""
    if r.returncode != 0 or http != "200" or '"ok":true' not in body:
        raise RuntimeError(f"sendDocument failed: {http} {body[:500]}")


BACKUP_SCRIPT = Path("/root/backup-server.sh")
BACKUP_DIR = Path("/root/backups")
TG_MAX_DOCUMENT_BYTES = 50 * 1024 * 1024  # лимит Bot API для sendDocument


def _latest_backup() -> Path | None:
    if not BACKUP_DIR.is_dir():
        return None
    files = sorted(
        BACKUP_DIR.glob("fornex-vps-*.tar.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def create_backup_archive() -> tuple[Path | None, str]:
    if not BACKUP_SCRIPT.is_file():
        return None, "Скрипт /root/backup-server.sh не найден"
    try:
        r = subprocess.run(
            ["bash", str(BACKUP_SCRIPT)],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return None, "Таймаут создания бэкапа (>5 мин)"
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "")[-400:]
        return None, f"Ошибка backup-server.sh:\n{tail}"
    archive = _latest_backup()
    if archive is None:
        return None, "Архив не найден в /root/backups/"
    return archive, ""


def run_backup_and_send(token: str, chat_id: int, *, create_new: bool = True) -> None:
    if create_new:
        send_message(token, chat_id, "⏳ Создаю бэкап сервера…", parse_mode=None)
        archive, err = create_backup_archive()
    else:
        send_message(token, chat_id, "⏳ Отправляю последний бэкап…", parse_mode=None)
        archive = _latest_backup()
        err = "" if archive else "Нет архивов в /root/backups/\nСначала: /backup"

    if err or archive is None:
        send_message(token, chat_id, f"❌ {err or 'бэкап не найден'}", parse_mode=None, reply_markup=kb_main())
        return

    size = archive.stat().st_size
    if size > TG_MAX_DOCUMENT_BYTES:
        send_message(
            token, chat_id,
            f"❌ Архив слишком большой для Telegram ({_human_size(size)}).\n"
            f"Лимит Bot API: 50 MB.\n\n"
            f"Скачайте через scp:\n"
            f"<code>scp root@103.75.126.216:{archive}</code>",
            reply_markup=kb_main(),
        )
        return

    caption = (
        f"📦 Бэкап Fornex VPS\n"
        f"{archive.name}\n"
        f"Размер: {_human_size(size)}\n"
        f"⚠️ Содержит секреты — храните в безопасном месте"
    )
    try:
        send_document(token, chat_id, archive, caption=caption)
        send_message(
            token, chat_id,
            "✅ Бэкап отправлен.\n"
            "В архиве: Xray, Hysteria, бот, systemd, cron.\n"
            "Храните офлайн, не пересылайте посторонним.",
            parse_mode=None,
            reply_markup=kb_main(),
        )
    except Exception as e:
        send_message(
            token, chat_id,
            f"⚠️ Не удалось отправить файл: {e}\n\n"
            f"Скачайте вручную:\n"
            f"<code>scp root@103.75.126.216:{archive}</code>",
            reply_markup=kb_main(),
        )


def send_subscription(token: str, chat_id: int, cl) -> None:
    """Файл со ссылками для Happ + URL для V2RayTun (если есть LE-сертификат)."""
    url, n = cl.subscription_stats()
    export = cl.SUB_EXPORT_FILE
    try:
        cl.export_subscription_txt(export)
    except Exception as e:
        send_message(token, chat_id, f"Ошибка экспорта: {e}")
        return

    send_message(token, chat_id, cl.format_subscription_help(n), reply_markup=kb_sub())

    try:
        send_document(
            token, chat_id, export,
            caption=f"VPN профили ({n} шт.) — для Happ: Поделиться → Открыть в Happ",
        )
    except Exception as e:
        send_message(
            token, chat_id,
            f"⚠️ Файл не отправился: {e}\nИспользуйте /links all",
            parse_mode=None,
        )

    from pathlib import Path as _P
    if _P(cl.LE_CERT).is_file():
        send_copyable_text(token, chat_id, "👇 URL подписки — следующее сообщение, только ссылка:")
        send_copyable_text(token, chat_id, url, reply_markup=kb_sub())
    else:
        send_message(
            token, chat_id,
            "ℹ️ URL https:// подписки в Happ пока не работает (нет Let's Encrypt).\n"
            "Используйте файл выше или /links iphone",
            parse_mode=None,
        )


def kb_links() -> dict:
    return {"inline_keyboard": [
        [_btn("📱 iPhone", "l:iphone"), _btn("🌐 Роутер", "l:router")],
        [_btn("📲 Android", "l:android"), _btn("⚡ HY2", "l:hy2")],
        [_btn("📡 :2053", "l:2053"), _btn("📡 :2096", "l:2096")],
        [_btn("📋 Все ссылки", "l:all")],
        [_btn("◀️ Главное меню", "m:main")],
    ]}


def kb_sni() -> dict:
    return {"inline_keyboard": [
        [_btn("microsoft", "s:ask:microsoft"), _btn("google", "s:ask:google")],
        [_btn("samsung", "s:ask:samsung"), _btn("apple", "s:ask:apple")],
        [_btn("yahoo", "s:ask:yahoo"), _btn("cloudflare", "s:ask:cloudflare")],
        [_btn("ℹ️ Текущий SNI", "s:show")],
        [_btn("◀️ Главное меню", "m:main")],
    ]}


def kb_restart() -> dict:
    return {"inline_keyboard": [
        [_btn("🔄 Xray", "r:ask:xray"), _btn("⚡ Hysteria2", "r:ask:hy2")],
        [_btn("◀️ Главное меню", "m:main")],
    ]}


def kb_confirm(action: str, target: str) -> dict:
    return {"inline_keyboard": [
        [_btn("✅ Да", f"{action}:ok:{target}"), _btn("❌ Отмена", "m:main")],
    ]}


def load_config() -> dict[str, str]:
    env: dict[str, str] = {}
    if not CONFIG.is_file():
        print("Нет config.env", file=sys.stderr)
        sys.exit(1)
    for raw in CONFIG.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def allowed_chats(cfg: dict[str, str]) -> set[int]:
    s: set[int] = set()
    if cfg.get("TG_CHAT_ID", "").strip().lstrip("-").isdigit():
        s.add(int(cfg["TG_CHAT_ID"].strip()))
    if CHAT_FILE.is_file():
        for line in CHAT_FILE.read_text().splitlines():
            t = line.strip()
            if t.lstrip("-").isdigit():
                s.add(int(t))
    return s


def read_offset() -> int:
    try:
        return int(OFFSET_FILE.read_text().strip())
    except (OSError, ValueError):
        return 0


def write_offset(o: int) -> None:
    OFFSET_FILE.write_text(str(o))


_TELEGRAM_API_IPS = (
    "149.154.167.220",
    "149.154.167.222",
    "149.154.175.100",
    "149.154.175.50",
)


def _telegram_api_host() -> str:
    cached = getattr(_telegram_api_host, "_cached", None)
    if cached:
        return cached
    try:
        socket.getaddrinfo("api.telegram.org", 443, type=socket.SOCK_STREAM)
        _telegram_api_host._cached = "api.telegram.org"  # type: ignore[attr-defined]
        return "api.telegram.org"
    except OSError:
        pass
    for ip in _TELEGRAM_API_IPS:
        try:
            with socket.create_connection((ip, 443), timeout=5):
                _telegram_api_host._cached = ip  # type: ignore[attr-defined]
                return ip
        except OSError:
            continue
    return "api.telegram.org"


def _urlopen(req: urllib.request.Request, timeout: float):
    host = _telegram_api_host()
    url = req.get_full_url()
    hdrs = {k: v for k, v in req.header_items()}
    if host != "api.telegram.org" and "api.telegram.org" in url:
        url = url.replace("api.telegram.org", host, 1)
        hdrs["Host"] = "api.telegram.org"
    req2 = urllib.request.Request(
        url, data=req.data, headers=hdrs, method=req.get_method(),
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(req2, timeout=timeout)


def http_get_json(url: str, timeout: float = 70.0) -> dict:
    with _urlopen(urllib.request.Request(url, method="GET"), timeout=timeout) as r:
        return json.loads(r.read().decode())


def post_json(url: str, payload: dict, timeout: float = 60.0) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, str(DIR / filename))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{filename} не найден")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def vpn_mod():
    return load_module("vpn_mgr", "vpn_manager.py")


def clients_mod():
    return load_module("clients_mgr", "clients.py")


def send_message(
    token: str,
    chat_id: int,
    text: str,
    parse_mode: str | None = "HTML",
    reply_markup: dict | None = None,
) -> None:
    payload: dict = {"chat_id": chat_id, "text": text[:4096]}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    post_json(f"https://api.telegram.org/bot{token}/sendMessage", payload)


def send_copyable_text(
    token: str,
    chat_id: int,
    text: str,
    reply_markup: dict | None = None,
) -> None:
    """Сообщение без разметки — в Telegram удобно «удержать → Копировать»."""
    payload: dict = {"chat_id": chat_id, "text": text[:4096]}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    post_json(f"https://api.telegram.org/bot{token}/sendMessage", payload)


def send_links_copyable(
    token: str,
    chat_id: int,
    vpn,
    profile: str,
    reply_markup: dict | None = None,
) -> None:
    """Подпись отдельно, ссылка — отдельным сообщением (только URL)."""
    entries = vpn.link_entries_for_profile(profile)
    copyable = [
        e for e in entries
        if (e.get("link") or "").startswith(("vless://", "hysteria2://", "http://", "https://"))
    ]
    if not copyable:
        send_copyable_text(
            token, chat_id,
            entries[0]["link"] if entries else f"Профиль «{profile}» не найден",
            reply_markup=reply_markup,
        )
        return

    if len(copyable) == 1:
        e = copyable[0]
        send_copyable_text(token, chat_id, e.get("label", "Ссылка"))
        send_copyable_text(token, chat_id, e["link"], reply_markup=reply_markup)
        return

    send_copyable_text(
        token, chat_id,
        f"🔗 {len(copyable)} ссылок — ниже каждая отдельным сообщением.\n"
        "Удержите сообщение со ссылкой → Копировать.",
    )
    for i, e in enumerate(copyable, 1):
        send_copyable_text(token, chat_id, f"{i}. {e.get('label', 'link')}")
        send_copyable_text(token, chat_id, e["link"])
    if reply_markup:
        send_copyable_text(token, chat_id, "✅ Готово", reply_markup=reply_markup)


_LINK_RE = re.compile(r"(vless://\S+|hysteria2://\S+|https?://\S+)")


def send_text_with_links(
    token: str,
    chat_id: int,
    text: str,
    reply_markup: dict | None = None,
    *,
    html_mode: bool = False,
) -> None:
    """Текст отдельно, каждая ссылка — отдельным сообщением."""
    links = _LINK_RE.findall(text)
    if not links:
        send_message(
            token, chat_id, text,
            parse_mode="HTML" if html_mode else None,
            reply_markup=reply_markup,
        )
        return
    body = _LINK_RE.sub("", text)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if body:
        send_message(
            token, chat_id, body,
            parse_mode="HTML" if html_mode else None,
        )
    if len(links) == 1:
        send_copyable_text(token, chat_id, "👇 Ссылка — следующее сообщение:")
    for i, link in enumerate(links):
        mk = reply_markup if i == len(links) - 1 else None
        send_copyable_text(token, chat_id, link, reply_markup=mk)


def send_messages_plain(
    token: str,
    chat_id: int,
    text: str,
    chunk: int = 3800,
    reply_markup: dict | None = None,
) -> None:
    if len(text) <= chunk:
        payload: dict = {"chat_id": chat_id, "text": text[:4096]}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        post_json(f"https://api.telegram.org/bot{token}/sendMessage", payload)
        return
    parts: list[str] = []
    buf = ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > chunk and buf:
            parts.append(buf)
            buf = line
        else:
            buf += line
    if buf:
        parts.append(buf)
    for i, part in enumerate(parts, 1):
        header = f"[{i}/{len(parts)}]\n" if len(parts) > 1 else ""
        mk = reply_markup if i == len(parts) else None
        send_message(token, chat_id, header + part, parse_mode=None, reply_markup=mk)


def answer_callback(token: str, callback_id: str, text: str = "") -> None:
    payload: dict = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text[:200]
    try:
        post_json(f"https://api.telegram.org/bot{token}/answerCallbackQuery", payload)
    except Exception:
        pass


def send_photo(token: str, chat_id: int, png: Path, caption: str) -> None:
    env = os.environ.copy()
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(k, None)
    r = subprocess.run(
        [
            "curl", "-sS", "--noproxy", "*", "-w", "\n%{http_code}",
            "-X", "POST", f"https://api.telegram.org/bot{token}/sendPhoto",
            "-F", f"chat_id={chat_id}",
            "-F", f"photo=@{png}",
            "-F", f"caption={caption}",
        ],
        capture_output=True, text=True, env=env, timeout=120,
    )
    out = r.stdout or ""
    parts = out.rsplit("\n", 1)
    body = parts[0] if len(parts) == 2 else out
    http = parts[1] if len(parts) == 2 else ""
    if r.returncode != 0 or http != "200" or '"ok":true' not in body:
        raise RuntimeError(f"sendPhoto failed: {http} {body[:500]}")


def format_status_text() -> str:
    mod = load_module("sec_report", "report.py")
    s = mod.collect_stats()
    vpn = vpn_mod()
    ib = vpn.main_443_inbound()
    sni_line = f"SNI :443 → <code>{html.escape(ib['sni'])}</code>" if ib else ""
    lines = [
        "<b>🔐 Безопасность</b>",
        html.escape(s["date"]),
        "",
        f"SSH: <code>{html.escape(s['ssh'])}</code>  |  Xray: <code>{html.escape(s['xray'])}</code>",
        sni_line,
        f"SSH brute (сегодня): <code>{s['ssh_failed_today']}</code>",
        f"CPU: <code>{html.escape(s.get('cpu_usage', 'n/a'))}</code>  "
        f"RAM: <code>{html.escape(s.get('mem_used', '?'))}</code>",
        "",
        f"Xray {s['report_days']}д: accepted <code>{s['xray_accepted']:,}</code>, "
        f"блок <code>{s['xray_blocked_pct']:.1f}%</code>",
    ]
    return "\n".join(l for l in lines if l)


def run_report_and_send(token: str, chat_id: int) -> None:
    png = Path("/tmp/security_report_bot.png")
    env = os.environ.copy()
    env["SECURITY_REPORT_PNG"] = str(png)
    env.setdefault("REPORT_PERIOD_DAYS", "7")
    env.setdefault("REPORT_TITLE", "Безопасность сервера — отчёт")
    subprocess.run([sys.executable, str(DIR / "report.py")], env=env, check=True, timeout=300)
    send_photo(token, chat_id, png, time.strftime("Отчёт %Y-%m-%d %H:%M %Z"))


def set_commands(token: str) -> None:
    cmds = [
        {"command": "menu", "description": "Кнопочное меню"},
        {"command": "check", "description": "Диагностика сервера"},
        {"command": "vpn", "description": "Статус VPN"},
        {"command": "links", "description": "VLESS-ссылки"},
        {"command": "qr", "description": "QR-код ссылки"},
        {"command": "test", "description": "Тест VPN-подключения"},
        {"command": "manage", "description": "Вкл/выкл клиентов"},
        {"command": "sub", "description": "Подписочная ссылка"},
        {"command": "newclient", "description": "Новый UUID-клиент"},
        {"command": "users", "description": "Список всех клиентов"},
        {"command": "backup", "description": "Создать и скачать бэкап"},
        {"command": "help", "description": "Справка"},
    ]
    try:
        post_json(f"https://api.telegram.org/bot{token}/setMyCommands", {"commands": cmds})
    except urllib.error.HTTPError:
        pass


PLAIN_ALIASES: dict[str, str] = {
    "меню": "/menu",
    "menu": "/menu",
    "старт": "/start",
    "start": "/start",
    "помощь": "/help",
    "help": "/help",
    "справка": "/help",
    "статус": "/vpn",
    "vpn": "/vpn",
    "ссылки": "/links",
    "links": "/links",
    "проверка": "/check",
    "check": "/check",
    "диагностика": "/check",
    "клиенты": "/clients",
    "clients": "/clients",
    "диск": "/disk",
    "логи": "/logs",
    "отчёт": "/report",
    "отчет": "/report",
    "тест": "/test iphone",
    "qr": "/qr iphone",
    "управление": "/manage",
    "подписка": "/sub",
    "пользователи": "/users",
}


def send_qr_for_profile(token: str, chat_id: int, vpn, profile: str) -> None:
    links = vpn.links_for_profile(profile)
    if not links or not links[0].startswith(("vless://", "hysteria2://")):
        send_message(token, chat_id, f"Нет ссылки для {profile}", parse_mode=None, reply_markup=kb_qr())
        return
    link = links[0]
    png = Path(f"/tmp/vpn_qr_{profile}.png")
    err = vpn.generate_qr_png(link, png)
    if err:
        send_message(token, chat_id, f"QR ошибка: {err}\n\n{link[:200]}…", parse_mode=None, reply_markup=kb_qr())
        return
    cap = f"QR — {profile}\nV2RayTun → + → сканировать QR"
    send_photo(token, chat_id, png, cap)
    send_copyable_text(token, chat_id, "Ссылка — следующее сообщение:")
    send_copyable_text(token, chat_id, link, reply_markup=kb_qr())


def run_vpn_test_and_reply(token: str, chat_id: int, vpn, profile: str) -> None:
    send_message(
        token, chat_id,
        f"🔌 Включите VPN ({profile}) в течение 5 сек…\nЖду подключение ~50 сек.",
        parse_mode=None,
    )
    result = vpn.run_vpn_connect_test(profile, wait_sec=50)
    send_message(token, chat_id, result, parse_mode=None, reply_markup=kb_test())


def normalize_input(text: str) -> str:
    t = text.strip()
    low = t.lower()
    if low in PLAIN_ALIASES:
        return PLAIN_ALIASES[low]
    return t


def handle_limited_command(token: str, chat_id: int, text: str) -> None:
    text = normalize_input(text)
    parts = text.split()
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]
    vpn = vpn_mod()
    cl = clients_mod()
    kb = _kb_for("limited")
    kbl = _kb_links_for("limited")

    if cmd in ("/start",):
        send_message(token, chat_id, WELCOME_LIMITED, reply_markup=kb)
    elif cmd in ("/help",):
        send_message(token, chat_id, HELP_LIMITED, reply_markup=kb)
    elif cmd == "/menu":
        send_message(token, chat_id, "📱 <b>Меню</b>", reply_markup=kb)
    elif cmd == "/links":
        profile = " ".join(args).strip() if args else "all"
        send_links_copyable(token, chat_id, vpn, profile, reply_markup=kbl)
    elif cmd == "/qr":
        profile = args[0] if args else "iphone"
        send_qr_for_profile(token, chat_id, vpn, profile)
    elif cmd in ("/newclient", "/addclient"):
        if not args:
            send_message(
                token, chat_id,
                "Пример: /newclient SergPhone\nСоздаст новый VPN-ключ.",
                parse_mode=None, reply_markup=kb,
            )
            return
        name = " ".join(args)
        limit = 0.0
        if args[-1].replace(".", "", 1).isdigit():
            limit = float(args[-1])
            name = " ".join(args[:-1]).strip()
        if not name:
            send_message(token, chat_id, "Укажите имя, например: /newclient SergPhone", reply_markup=kb)
            return
        send_text_with_links(token, chat_id, cl.add_client(name, limit), html_mode=True)
        send_message(token, chat_id, "✅ Готово", reply_markup=kb)
    elif cmd in ac.LIMITED_COMMANDS:
        send_message(token, chat_id, "Используйте /menu", reply_markup=kb)
    else:
        send_message(token, chat_id, DENY_ADMIN_ONLY, reply_markup=kb)


def handle_limited_callback(token: str, chat_id: int, callback_id: str, data: str) -> None:
    vpn = vpn_mod()
    cl = clients_mod()
    answer_callback(token, callback_id)
    kb = _kb_for("limited")
    kbl = _kb_links_for("limited")

    if data == "m:main":
        send_message(token, chat_id, "📱 <b>Меню</b>", reply_markup=kb)
    elif data == "m:links":
        send_message(token, chat_id, "Выберите профиль:", reply_markup=kbl)
    elif data == "m:help":
        send_message(token, chat_id, HELP_LIMITED, reply_markup=kb)
    elif data == "m:newclient":
        send_message(
            token, chat_id,
            "➕ <b>Новый ключ</b>\n\nНапишите:\n<code>/newclient Имя</code>\n\n"
            "Пример: <code>/newclient SergPhone</code>",
            reply_markup=kb,
        )
    elif data.startswith("l:"):
        send_links_copyable(token, chat_id, vpn, data[2:], reply_markup=kbl)
    elif data.startswith("q:"):
        send_qr_for_profile(token, chat_id, vpn, data[2:])
    else:
        send_message(token, chat_id, DENY_ADMIN_ONLY, reply_markup=kb)


def handle_command(token: str, chat_id: int, text: str, role: str = "admin") -> None:
    if role == "limited":
        handle_limited_command(token, chat_id, text)
        return
    text = normalize_input(text)
    parts = text.split()
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]
    vpn = vpn_mod()
    cl = clients_mod()

    if cmd in ("/start",):
        send_message(token, chat_id, WELCOME + "\n\n" + vpn.format_dashboard(), parse_mode=None, reply_markup=kb_main())
    elif cmd in ("/help",):
        send_message(token, chat_id, HELP_TEXT, reply_markup=kb_main())
    elif cmd == "/menu":
        send_message(token, chat_id, "📱 <b>Главное меню</b>", reply_markup=kb_main())
    elif cmd == "/check":
        send_message(token, chat_id, vpn.format_health_check(), parse_mode=None, reply_markup=kb_main())
    elif cmd == "/status":
        send_message(token, chat_id, format_status_text(), reply_markup=kb_main())
    elif cmd == "/vpn":
        send_message(token, chat_id, vpn.format_vpn_status(), parse_mode=None, reply_markup=kb_main())
    elif cmd == "/clients":
        send_message(token, chat_id, vpn.format_clients_report(), parse_mode=None, reply_markup=kb_main())
    elif cmd == "/report":
        send_message(token, chat_id, "⏳ Генерирую отчёт…", parse_mode=None)
        run_report_and_send(token, chat_id)
        send_message(token, chat_id, "✅ Готово", reply_markup=kb_main())
    elif cmd == "/disk":
        send_message(token, chat_id, vpn.format_disk_report(), parse_mode=None, reply_markup=kb_main())
    elif cmd == "/cleanup":
        send_message(
            token, chat_id,
            "🧹 Запустить безопасную очистку?\n(логи, кэш apt, старые Cursor)",
            parse_mode=None,
            reply_markup=kb_confirm("c", "clean"),
        )
    elif cmd == "/backup":
        if args and args[0].lower() in ("last", "последний"):
            run_backup_and_send(token, chat_id, create_new=False)
        else:
            send_message(
                token, chat_id,
                "📦 <b>Создать бэкап и отправить в Telegram?</b>\n\n"
                "В архиве будут секреты (токен бота, ключи VPN).\n"
                "Лимит Telegram: <b>50 MB</b> (сейчас архив ~50 KB).\n\n"
                "Последний без создания: <code>/backup last</code>",
                reply_markup=kb_confirm("b", "create"),
            )
    elif cmd == "/logs":
        n = int(args[0]) if args and args[0].isdigit() else 15
        send_message(token, chat_id, vpn.tail_access_log(n), parse_mode=None, reply_markup=kb_main())
    elif cmd == "/links":
        profile = " ".join(args).strip() if args else "all"
        send_links_copyable(token, chat_id, vpn, profile, reply_markup=kb_links())
    elif cmd == "/sni":
        if not args:
            send_message(token, chat_id, vpn.format_sni_info(), parse_mode=None, reply_markup=kb_sni())
        else:
            send_message(
                token, chat_id,
                f"Сменить SNI на <b>{html.escape(args[0])}</b>?\nПодключения на :443 оборвутся.",
                reply_markup=kb_confirm("s", args[0]),
            )
    elif cmd == "/restart":
        name = args[0] if args else "xray"
        send_message(
            token, chat_id,
            f"Перезапустить <b>{html.escape(name)}</b>?",
            reply_markup=kb_confirm("r", name),
        )
    elif cmd == "/qr":
        profile = args[0] if args else "iphone"
        send_qr_for_profile(token, chat_id, vpn, profile)
    elif cmd == "/test":
        profile = args[0] if args else "iphone"
        run_vpn_test_and_reply(token, chat_id, vpn, profile)
    elif cmd == "/manage":
        send_message(token, chat_id, vpn.format_manage_clients(), parse_mode=None, reply_markup=kb_manage(vpn, cl))
    elif cmd == "/users":
        send_message(token, chat_id, cl.format_clients_registry(), reply_markup=kb_main())
    elif cmd == "/clients":
        send_message(token, chat_id, vpn.format_clients_report(), parse_mode=None, reply_markup=kb_main())
    elif cmd == "/sub":
        send_subscription(token, chat_id, cl)
    elif cmd in ("/newclient", "/addclient"):
        if not args:
            send_message(token, chat_id, "Пример: /newclient Друг\nИли: /newclient Друг 50 (лимит 50 ГБ)")
            return
        name = " ".join(args)
        limit = 0.0
        if args[-1].replace(".", "", 1).isdigit():
            limit = float(args[-1])
            name = " ".join(args[:-1]).strip()
        if not name:
            send_message(token, chat_id, "Укажите имя клиента")
            return
        send_text_with_links(token, chat_id, cl.add_client(name, limit), html_mode=True)
    elif cmd == "/limit":
        if len(args) < 2:
            send_message(token, chat_id, "Пример: /limit iphone 100\n0 = без лимита")
            return
        try:
            gb = float(args[-1])
            ident = " ".join(args[:-1])
            send_message(token, chat_id, cl.set_client_limit(ident, gb))
        except ValueError:
            send_message(token, chat_id, "Лимит должен быть числом (ГБ)")
    elif cmd == "/revoke":
        if not args:
            send_message(token, chat_id, "Пример: /revoke Друг")
            return
        ident = " ".join(args)
        send_message(
            token, chat_id,
            f"Удалить клиента <b>{html.escape(ident)}</b> навсегда?\nКлюч перестанет работать.",
            reply_markup=kb_confirm("x", f"revoke:{ident}"),
        )
    else:
        send_message(token, chat_id, "Неизвестная команда. /menu", reply_markup=kb_main())


def handle_callback(token: str, chat_id: int, callback_id: str, data: str, role: str = "admin") -> None:
    if role == "limited":
        if not ac.limited_callback_allowed(data):
            answer_callback(token, callback_id, "⛔")
            send_message(token, chat_id, DENY_ADMIN_ONLY, reply_markup=kb_main_limited())
            return
        handle_limited_callback(token, chat_id, callback_id, data)
        return
    vpn = vpn_mod()
    cl = clients_mod()
    answer_callback(token, callback_id)

    if data == "m:main":
        send_message(token, chat_id, "📱 <b>Главное меню</b>", reply_markup=kb_main())
    elif data == "m:dash":
        send_message(token, chat_id, vpn.format_dashboard(), parse_mode=None, reply_markup=kb_main())
    elif data == "m:check":
        send_message(token, chat_id, vpn.format_health_check(), parse_mode=None, reply_markup=kb_main())
    elif data == "m:links":
        send_message(token, chat_id, "Выберите профиль:", reply_markup=kb_links())
    elif data == "m:sni":
        send_message(token, chat_id, vpn.format_sni_info(), parse_mode=None, reply_markup=kb_sni())
    elif data == "m:clients":
        send_message(token, chat_id, vpn.format_clients_report(), parse_mode=None, reply_markup=kb_main())
    elif data == "m:logs":
        send_message(token, chat_id, vpn.tail_access_log(15), parse_mode=None, reply_markup=kb_main())
    elif data == "m:disk":
        send_message(token, chat_id, vpn.format_disk_report(), parse_mode=None, reply_markup=kb_main())
    elif data == "m:backup":
        send_message(
            token, chat_id,
            "📦 <b>Создать бэкап и отправить в Telegram?</b>\n\n"
            "В архиве будут секреты (токен бота, ключи VPN).\n"
            "Лимит Telegram: <b>50 MB</b>.",
            reply_markup=kb_confirm("b", "create"),
        )
    elif data == "b:ok:create":
        run_backup_and_send(token, chat_id, create_new=True)
    elif data == "m:restart":
        send_message(token, chat_id, "Что перезапустить?", reply_markup=kb_restart())
    elif data == "m:report":
        send_message(token, chat_id, "⏳ Генерирую отчёт…", parse_mode=None)
        run_report_and_send(token, chat_id)
        send_message(token, chat_id, "✅ Готово", reply_markup=kb_main())
    elif data == "m:help":
        send_message(token, chat_id, HELP_TEXT, reply_markup=kb_main())
    elif data.startswith("l:"):
        profile = data[2:]
        send_links_copyable(token, chat_id, vpn, profile, reply_markup=kb_links())
    elif data == "s:show":
        send_message(token, chat_id, vpn.format_sni_info(), parse_mode=None, reply_markup=kb_sni())
    elif data.startswith("s:ask:"):
        sni = data[6:]
        send_message(
            token, chat_id,
            f"Сменить SNI на <b>{html.escape(sni)}</b>?\nТекущие VPN-сессии на :443 оборвутся.",
            reply_markup=kb_confirm("s", sni),
        )
    elif data.startswith("s:ok:"):
        sni = data[5:]
        send_message(token, chat_id, f"⏳ Меняю SNI → {sni}…", parse_mode=None)
        result = vpn.change_main_sni(sni)
        send_text_with_links(token, chat_id, result, reply_markup=kb_main())
    elif data.startswith("r:ask:"):
        svc = data[6:]
        send_message(
            token, chat_id,
            f"Перезапустить <b>{html.escape(svc)}</b>?",
            reply_markup=kb_confirm("r", svc),
        )
    elif data.startswith("r:ok:"):
        svc = data[5:]
        result = vpn.restart_service(svc)
        send_message(token, chat_id, result, parse_mode=None, reply_markup=kb_main())
    elif data == "c:ok:clean":
        send_message(token, chat_id, "⏳ Очистка…", parse_mode=None)
        result = vpn.run_safe_cleanup()
        send_message(token, chat_id, result, parse_mode=None, reply_markup=kb_main())
    elif data == "m:test":
        send_message(token, chat_id, "Кого проверяем? Включите VPN после выбора.", reply_markup=kb_test())
    elif data == "m:manage":
        send_message(token, chat_id, vpn.format_manage_clients(), parse_mode=None, reply_markup=kb_manage(vpn, cl))
    elif data == "m:sub":
        send_subscription(token, chat_id, cl)
    elif data == "sub:url":
        send_subscription(token, chat_id, cl)
    elif data == "m:users":
        send_message(token, chat_id, cl.format_clients_registry(), reply_markup=kb_main())
    elif data.startswith("t:"):
        run_vpn_test_and_reply(token, chat_id, vpn, data[2:])
    elif data.startswith("q:"):
        send_qr_for_profile(token, chat_id, vpn, data[2:])
    elif data.startswith("u:ask:"):
        # u:ask:off:iphone
        rest = data[8:]
        if ":" in rest:
            action, profile = rest.split(":", 1)
            verb = "Выключить" if action == "off" else "Включить"
            send_message(
                token, chat_id,
                f"{verb} <b>{html.escape(profile)}</b>?",
                reply_markup=kb_confirm("u", f"{action}:{profile}"),
            )
    elif data.startswith("u:ok:"):
        # u:ok:off:iphone
        rest = data[5:]
        if ":" in rest:
            action, profile = rest.split(":", 1)
            enable = action == "on"
            result = vpn.set_client_enabled(profile, enable)
            send_message(token, chat_id, result, parse_mode=None, reply_markup=kb_manage(vpn, cl))
    elif data.startswith("x:ok:revoke:"):
        ident = data[12:]
        send_message(token, chat_id, cl.revoke_client(ident))
    else:
        send_message(token, chat_id, "?", reply_markup=kb_main())


def main() -> None:
    cfg = load_config()
    token = cfg.get("TG_BOT_TOKEN", "")
    if not token:
        print("Нет TG_BOT_TOKEN", file=sys.stderr)
        sys.exit(1)
    allow = ac.admin_chat_ids(cfg)
    if not allow:
        print("Нет tg_chat_id", file=sys.stderr)
        sys.exit(1)

    try:
        set_commands(token)
    except Exception as e:
        print(f"[!] setMyCommands: {e}", flush=True)

    offset = read_offset()
    base = f"https://api.telegram.org/bot{token}"
    last_alert_check = 0.0
    last_sni_check = 0.0

    try:
        cl = clients_mod()
        cl.sync_registry_from_xray()
        from sub_server import start_subscription_server
        start_subscription_server()
    except Exception as e:
        print(f"[!] clients/sub init: {e}", flush=True)

    print(
        f"[+] VPN-бот v6 (роли), offset={offset}, admins={allow}, "
        f"guests={ac.limited_chat_ids()}",
        flush=True,
    )

    while True:
        now = time.time()
        if now - last_alert_check >= 300:
            last_alert_check = now
            try:
                vpn = vpn_mod()
                for alert_msg in vpn.poll_alerts():
                    for cid in allow:
                        try:
                            send_message(token, cid, alert_msg)
                        except Exception as ae:
                            print(f"[!] alert send: {ae}", flush=True)
            except Exception as ae:
                print(f"[!] poll_alerts: {ae}", flush=True)

        if now - last_sni_check >= 86400:
            last_sni_check = now
            try:
                vpn = vpn_mod()
                for alert_msg in vpn.poll_sni_rotation():
                    for cid in allow:
                        try:
                            send_message(token, cid, alert_msg)
                        except Exception as se:
                            print(f"[!] sni rotation send: {se}", flush=True)
            except Exception as se:
                print(f"[!] poll_sni_rotation: {se}", flush=True)

        try:
            url = base + "/getUpdates?" + urllib.parse.urlencode({"timeout": 55, "offset": offset + 1})
            data = http_get_json(url, timeout=70.0)
        except Exception as e:
            print(f"[!] getUpdates: {e}", flush=True)
            time.sleep(5)
            continue

        for u in data.get("result", []):
            uid = u["update_id"]
            offset = uid
            write_offset(offset)

            cb = u.get("callback_query")
            if cb:
                chat = (cb.get("message") or {}).get("chat") or {}
                cid = chat.get("id")
                if cid is None:
                    continue
                from_user = cb.get("from") or {}
                username = from_user.get("username")
                role = ac.get_role(cid, username, cfg)
                if role == "denied":
                    answer_callback(token, cb.get("id", ""), "⛔")
                    continue
                try:
                    handle_callback(token, cid, cb.get("id", ""), cb.get("data", ""), role=role)
                except Exception as e:
                    try:
                        send_message(token, cid, f"Ошибка: {html.escape(str(e)[:300])}")
                    except Exception:
                        print(f"callback error: {e}", flush=True)
                continue

            msg = u.get("message") or u.get("edited_message")
            if not msg:
                continue
            chat = msg.get("chat") or {}
            cid = chat.get("id")
            if cid is None:
                continue
            from_user = msg.get("from") or {}
            username = from_user.get("username")
            was_guest = cid in ac.limited_chat_ids()
            role = ac.get_role(cid, username, cfg)
            text = (msg.get("text") or "").strip()
            if not text:
                continue

            if role == "denied":
                try:
                    send_message(token, cid, "⛔ Доступ запрещён.")
                except Exception:
                    pass
                continue

            was_guest = cid in ac.limited_chat_ids()
            if role == "limited" and not was_guest:
                for admin_id in allow:
                    try:
                        send_message(
                            token, admin_id,
                            f"👤 Подключился гость <b>@{html.escape(username or '?')}</b>\n"
                            f"chat_id: <code>{cid}</code>\n"
                            f"Доступ: ссылки и /newclient",
                        )
                    except Exception:
                        pass

            plain_ok = False
            if role == "limited":
                low = text.lower()
                plain_ok = low in ("меню", "menu", "помощь", "help", "справка", "ссылки", "links")
            elif text.lower() in PLAIN_ALIASES:
                plain_ok = True

            if not text.startswith("/") and not plain_ok:
                send_message(
                    token, cid,
                    "Напишите /menu или «меню».\n/links — ссылки · /newclient Имя — новый ключ",
                    reply_markup=_kb_for(role),
                )
                continue

            try:
                handle_command(token, cid, text, role=role)
            except Exception as e:
                try:
                    send_message(token, cid, f"Ошибка: {html.escape(str(e)[:300])}")
                except Exception:
                    print(f"handler error: {e}", flush=True)


if __name__ == "__main__":
    main()
