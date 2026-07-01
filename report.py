#!/usr/bin/env python3
"""Security report generator for Telegram bot."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

OUT = Path(os.environ.get("SECURITY_REPORT_PNG", "/tmp/security_report.png"))
REPORT_DAYS = max(1, min(30, int(os.environ.get("REPORT_PERIOD_DAYS", "7"))))
REPORT_TITLE = os.environ.get("REPORT_TITLE", "Безопасность сервера — отчёт")

BLOCK_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("YouTube/Google", ("youtube", "googlevideo", "ytimg", "ggpht", "gstatic")),
    ("Meta/IG/FB", ("facebook", "fbcdn", "instagram", "whatsapp", "messenger")),
    ("X/Twitter", ("twitter", "twimg", "t.co", "pscp.tv")),
    ("Discord", ("discord", "discordapp", "discord.gg")),
    ("TikTok", ("tiktok", "tiktokcdn")),
    ("OpenAI", ("openai", "chatgpt", "oaistatic")),
    ("CDN/Other", ("cloudflare", "fastly", "akamai", "reddit", "spotify")),
)


def _read_first(path: str) -> str:
    try:
        return Path(path).read_text(errors="ignore")
    except Exception:
        return ""


def system_resources() -> dict:
    res = {"loadavg": "?", "mem_total": "?", "mem_used": "?"}
    la = _read_first("/proc/loadavg").strip().split()[:3]
    if la:
        res["loadavg"] = " ".join(la)
    try:
        meminfo = {}
        for line in Path("/proc/meminfo").read_text(errors="ignore").splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            meminfo[k.strip()] = v.strip().split()[0]
        total_kb = int(meminfo.get("MemTotal", "0"))
        avail_kb = int(meminfo.get("MemAvailable", "0"))
        used_kb = max(0, total_kb - avail_kb)
        res["mem_total"] = f"{total_kb/1024/1024:.1f} GiB"
        res["mem_used"] = f"{used_kb/1024/1024:.1f} GiB"
    except Exception:
        pass
    return res


def service_active(unit: str) -> str:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=6,
        )
        return (r.stdout or "").strip() or "unknown"
    except Exception:
        return "?"


def _count_today_lines_contains(substr: str, files: list[str]) -> int:
    today_iso = datetime.now().strftime("%Y-%m-%d")
    total = 0
    for fp in files:
        try:
            p = Path(fp)
            if not p.exists():
                continue
            if p.suffix == ".gz":
                r = subprocess.run(
                    ["zgrep", "-h", substr, str(p)],
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                lines = r.stdout.splitlines() if r.returncode in (0, 1) else []
            else:
                lines = p.read_text(errors="ignore").splitlines()
            for line in lines:
                if substr not in line:
                    continue
                if today_iso in line[:60] or today_iso.replace("-", "/") in line[:60]:
                    total += 1
        except Exception:
            continue
    return total


def ssh_stats_today() -> dict:
    base = "/var/log"
    files = [f"{base}/auth.log", *sorted(str(p) for p in Path(base).glob("auth.log.*"))]
    return {
        "ssh_failed_today": _count_today_lines_contains("Failed password", files),
        "ssh_invalid_today": _count_today_lines_contains("Invalid user", files),
    }


def xray_analyze(days: int) -> dict:
    access_log = Path("/var/log/xray/access.log")
    if not access_log.exists():
        return {
            "xray_log_ok": False,
            "xray_accepted": 0,
            "xray_blockedish": 0,
            "xray_blocked_pct": 0.0,
            "xray_top_cats": [],
        }
    prefixes = {(datetime.now().date() - timedelta(days=i)).strftime("%Y/%m/%d") for i in range(days)}
    total_accepted = 0
    blocked_hits = 0
    per_cat = defaultdict(int)
    try:
        with access_log.open("r", errors="ignore") as f:
            for line in f:
                if len(line) < 19 or line[:10] not in prefixes:
                    continue
                if " accepted " not in line:
                    continue
                total_accepted += 1
                low = line.lower()
                for cat, kws in BLOCK_CATEGORIES:
                    if any(k in low for k in kws):
                        per_cat[cat] += 1
                        blocked_hits += 1
                        break
    except Exception:
        return {
            "xray_log_ok": False,
            "xray_accepted": 0,
            "xray_blockedish": 0,
            "xray_blocked_pct": 0.0,
            "xray_top_cats": [],
        }
    pct = (100.0 * blocked_hits / total_accepted) if total_accepted else 0.0
    return {
        "xray_log_ok": True,
        "xray_accepted": total_accepted,
        "xray_blockedish": blocked_hits,
        "xray_blocked_pct": pct,
        "xray_top_cats": sorted(per_cat.items(), key=lambda x: -x[1])[:5],
    }


def xray_error_tail() -> str:
    p = Path("/var/log/xray/error.log")
    if not p.exists():
        return "нет лога"
    try:
        lines = [l for l in p.read_text(errors="ignore").splitlines() if l.strip()]
        return lines[-1][:90] if lines else "ok"
    except Exception:
        return "—"


def cpu_usage_percent(interval: float = 0.6) -> str:
    try:
        def read_cpu() -> tuple[int, int]:
            line = Path("/proc/stat").read_text(errors="ignore").splitlines()[0]
            nums = list(map(int, line.split()[1:]))
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
            return idle, sum(nums)

        idle1, total1 = read_cpu()
        time.sleep(interval)
        idle2, total2 = read_cpu()
        used = 1.0 - (max(0, idle2 - idle1) / max(1, total2 - total1))
        return f"{used * 100.0:.1f}%"
    except Exception:
        return "n/a"


def collect_stats() -> dict:
    s: dict = {}
    s.update(ssh_stats_today())
    s.update({
        "ssh": service_active("ssh"),
        "xray": service_active("xray"),
        "xray_error_tail": xray_error_tail(),
    })
    s.update(xray_analyze(REPORT_DAYS))
    s["cpu_usage"] = cpu_usage_percent()
    s.update(system_resources())
    s.update({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "report_days": REPORT_DAYS,
        "report_title": REPORT_TITLE,
    })
    return s


def draw_dashboard(s: dict, out: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("Install Pillow: apt install -y python3-pil", file=sys.stderr)
        sys.exit(2)

    W, H = 900, 740
    bg, card, accent = (15, 30, 60), (30, 50, 90), (255, 193, 7)
    text, muted, ok_green = (240, 245, 255), (160, 175, 200), (100, 220, 140)
    img = Image.new("RGB", (W, H), bg)
    dr = ImageDraw.Draw(img)

    def font(sz: int):
        for name in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ):
            p = Path(name)
            if p.is_file():
                return ImageFont.truetype(str(p), sz)
        return ImageFont.load_default()

    f_title, f_big, f_mid, f_small, f_tiny = font(26), font(22), font(18), font(14), font(13)
    dr.text((40, 22), s["report_title"], fill=text, font=f_title)
    dr.text((40, 56), f"{s['date']} · период: {s['report_days']} дн.", fill=muted, font=f_small)

    def add_card(x1, y1, x2, y2, title, value):
        dr.rounded_rectangle((x1, y1, x2, y2), radius=12, fill=card, outline=accent, width=1)
        dr.text((x1 + 14, y1 + 10), title, fill=muted, font=f_small)
        v = str(value)
        color = ok_green if v.lower() == "active" else accent if v.isdigit() else text
        dr.text((x1 + 14, y1 + 34), v, fill=color, font=f_big if len(v) < 20 else f_mid)

    add_card(40, 90, 400, 200, "SSH: неверный пароль (сегодня)", s["ssh_failed_today"])
    add_card(460, 90, 860, 200, "SSH: несуществ. пользователь (сегодня)", s["ssh_invalid_today"])
    add_card(40, 215, 400, 325, "Сервис SSH", s["ssh"])
    add_card(460, 215, 860, 325, "Сервис Xray", s["xray"])
    if s.get("xray_log_ok"):
        add_card(40, 340, 860, 440, "Xray: accepted / «режут»", f"{s['xray_accepted']:,} · {s['xray_blocked_pct']:.1f}%")
    else:
        add_card(40, 340, 860, 440, "Xray: логи недоступны", "n/a")

    y = 465
    dr.rounded_rectangle((40, y, 860, y + 125), radius=12, fill=card, outline=accent, width=1)
    dr.text((56, y + 12), "Топ категорий (access.log)", fill=muted, font=f_small)
    yy = y + 40
    top = s.get("xray_top_cats") or []
    if top:
        for cat, cnt in top[:5]:
            dr.text((56, yy), f"• {cat}: {cnt:,}", fill=accent, font=f_tiny)
            yy += 20
    else:
        dr.text((56, yy), "(нет данных)", fill=text, font=f_tiny)

    y2 = y + 140
    dr.rounded_rectangle((40, y2 - 20, 860, y2 + 60), radius=12, fill=card, outline=accent, width=1)
    dr.text((56, y2 - 8), "Ресурсы", fill=muted, font=f_small)
    dr.text((56, y2 + 12), f"loadavg: {s.get('loadavg','?')}", fill=text, font=f_tiny)
    dr.text((56, y2 + 32), f"RAM: {s.get('mem_used','?')} / {s.get('mem_total','?')}", fill=text, font=f_tiny)

    y3 = y2 + 70
    dr.rounded_rectangle((40, y3, 860, y3 + 55), radius=12, fill=card, outline=accent, width=1)
    dr.text((56, y3 + 10), "Xray error.log (хвост)", fill=muted, font=f_small)
    dr.text((56, y3 + 30), s.get("xray_error_tail", "—")[:90], fill=text, font=f_tiny)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")


def main() -> None:
    s = collect_stats()
    draw_dashboard(s, OUT)
    print(str(OUT))


if __name__ == "__main__":
    main()
