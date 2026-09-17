#!/bin/bash
# Постоянный Cloudflare Named Tunnel для wspr.online
# Запускать в SSH на VPS (не в Cursor-песочнице):
#   bash /root/couple-quiz-bot/setup-named-tunnel.sh
set -euo pipefail
cd /root/couple-quiz-bot

DOMAIN_HOST="app.wspr.online"
MINI_URL="https://${DOMAIN_HOST}/app/index.html"
TUNNEL_NAME="shepot"
API_PORT="${API_PORT:-8787}"

echo "=== 0) stop quick tunnel (*.trycloudflare.com) ==="
for pid in $(pgrep -x cloudflared 2>/dev/null || true); do
  cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
  case "$cmd" in
    *'tunnel --url '*) kill "$pid" 2>/dev/null || true ;;
  esac
done
sleep 1

echo "=== 1) cloudflared login (браузер → выберите wspr.online) ==="
if [ ! -f /root/.cloudflared/cert.pem ]; then
  cloudflared tunnel login
else
  echo "cert.pem уже есть — login пропускаем"
fi

echo "=== 2) create tunnel if needed ==="
if ! cloudflared tunnel list 2>/dev/null | grep -q " ${TUNNEL_NAME} "; then
  cloudflared tunnel create "$TUNNEL_NAME"
fi
cloudflared tunnel list

TUNNEL_ID=$(cloudflared tunnel list | awk -v n="$TUNNEL_NAME" '$2==n{print $1; exit}')
if [ -z "$TUNNEL_ID" ]; then
  echo "FAIL: не нашёл Tunnel ID для $TUNNEL_NAME"
  cloudflared tunnel list
  exit 1
fi
echo "TUNNEL_ID=$TUNNEL_ID"

CRED="/root/.cloudflared/${TUNNEL_ID}.json"
if [ ! -f "$CRED" ]; then
  echo "FAIL: нет файла credentials $CRED"
  ls -la /root/.cloudflared/
  exit 1
fi

mkdir -p /root/.cloudflared
cat > /root/.cloudflared/config.yml <<EOF
tunnel: ${TUNNEL_ID}
credentials-file: ${CRED}

ingress:
  - hostname: ${DOMAIN_HOST}
    service: http://127.0.0.1:${API_PORT}
  - service: http_status:404
EOF
echo "wrote /root/.cloudflared/config.yml"

echo "=== 3) DNS route app.wspr.online ==="
cloudflared tunnel route dns "$TUNNEL_NAME" "$DOMAIN_HOST" || true

echo "=== 4) .env MINI_APP_URL ==="
if grep -q '^MINI_APP_URL=' .env; then
  sed -i "s|^MINI_APP_URL=.*|MINI_APP_URL=${MINI_URL}|" .env
else
  echo "MINI_APP_URL=${MINI_URL}" >> .env
fi
grep '^MINI_APP_URL=' .env

# fallback в app.js на постоянный origin
python3 - <<PY
from pathlib import Path
import re
p = Path("/root/couple-quiz-bot/docs/app.js")
text = p.read_text()
text2, n = re.subn(
    r'(window\.SHEPOT_API_BASE\s*=\s*\n?\s*window\.SHEPOT_API_BASE\s*\|\|\s*")https?://[^"]+(")',
    r'\1https://app.wspr.online\2',
    text,
    count=1,
)
if n:
    p.write_text(text2)
    print("patched app.js fallback -> https://app.wspr.online")
else:
    print("app.js fallback patch skipped (pattern not found)")
PY

echo "=== 5) ensure API on :${API_PORT} ==="
if ! curl -sS -m 2 --noproxy '*' "http://127.0.0.1:${API_PORT}/api/health" | grep -q status; then
  echo "API не отвечает — поднимаю start.sh"
  bash /root/couple-quiz-bot/start.sh
  sleep 2
fi
curl -sS -m 3 --noproxy '*' "http://127.0.0.1:${API_PORT}/api/health"; echo
curl -sS -m 3 --noproxy '*' -o /dev/null -w "app_local:%{http_code}\n" "http://127.0.0.1:${API_PORT}/app/index.html"

echo "=== 6) run named tunnel in background ==="
pkill -f "cloudflared tunnel run ${TUNNEL_NAME}" 2>/dev/null || true
sleep 1
nohup cloudflared tunnel run "$TUNNEL_NAME" >/tmp/cloudflared-shepot.log 2>&1 &
echo "cfpid=$!"
sleep 3
tail -n 20 /tmp/cloudflared-shepot.log || true

echo "=== 7) update Telegram Menu Button ==="
.venv/bin/python - <<'PY'
import asyncio, os
from pathlib import Path
from dotenv import load_dotenv
from aiogram import Bot
from aiogram.types import MenuButtonWebApp, WebAppInfo

load_dotenv(Path("/root/couple-quiz-bot/.env"), override=True)
url = os.environ["MINI_APP_URL"]
async def main():
    bot = Bot(token=os.environ["BOT_TOKEN"])
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(text="Шёпот", web_app=WebAppInfo(url=url))
    )
    print("Menu Button ->", url)
    await bot.session.close()
asyncio.run(main())
PY

# перезапуск бота чтобы клавиатура с токеном брала новый MINI_APP_URL
bash /root/couple-quiz-bot/start.sh

echo
echo "READY"
echo "Проверьте с телефона:"
echo "  https://${DOMAIN_HOST}/api/health"
echo "  https://${DOMAIN_HOST}/app/index.html"
echo "В Telegram: /menu → «🌿 Открыть Шёпот»"
echo "Лог туннеля: tail -f /tmp/cloudflared-shepot.log"
