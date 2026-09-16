#!/bin/bash
# Поднять API+бот и Cloudflare Tunnel, обновить SHEPOT_API_BASE.
set -euo pipefail
cd /root/couple-quiz-bot

unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
unset SOCKS_PROXY SOCKS5_PROXY socks_proxy socks5_proxy
export NO_PROXY='*'
export no_proxy='*'

echo "=== stop old cloudflared ==="
pkill -9 -f 'cloudflared tunnel' 2>/dev/null || true
sleep 1

echo "=== free 8080 and restart bot ==="
fuser -k 8080/tcp 2>/dev/null || true
sleep 1
bash /root/couple-quiz-bot/start.sh

echo "=== wait for local health ==="
ok=0
for i in $(seq 1 25); do
  body=$(curl -sS -m 2 --noproxy '*' http://127.0.0.1:8080/api/health 2>/dev/null || true)
  if echo "$body" | grep -q ok; then
    ok=1
    break
  fi
  sleep 1
done
if [ "$ok" != "1" ]; then
  echo "FAIL: local /api/health"
  ss -tlnp | grep 8080 || true
  tail -n 50 bot.log
  exit 1
fi
echo "local health OK: $(curl -sS --noproxy '*' http://127.0.0.1:8080/api/health)"

echo "=== start cloudflared ==="
rm -f /tmp/cloudflared.log
nohup /usr/local/bin/cloudflared tunnel --url http://127.0.0.1:8080 >/tmp/cloudflared.log 2>&1 &
echo "cloudflared pid=$!"

TUNNEL=""
for i in $(seq 1 40); do
  TUNNEL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/cloudflared.log 2>/dev/null | head -1 || true)
  if [ -n "$TUNNEL" ]; then
    break
  fi
  sleep 1
done
if [ -z "$TUNNEL" ]; then
  echo "FAIL: no tunnel URL"
  tail -n 80 /tmp/cloudflared.log || true
  exit 1
fi
echo "TUNNEL=$TUNNEL"
echo "$TUNNEL" >/tmp/shepot_tunnel_url.txt

python3 - "$TUNNEL" <<'PY'
from pathlib import Path
import re
import sys
tunnel = sys.argv[1]
p = Path("/root/couple-quiz-bot/docs/app.js")
text = p.read_text()
text2, n = re.subn(r"https://[a-z0-9-]+\.trycloudflare\.com", tunnel, text, count=1)
if n == 0:
    raise SystemExit("could not patch SHEPOT_API_BASE in docs/app.js")
p.write_text(text2)
print("patched app.js ->", tunnel)
PY

echo "=== tunnel health ==="
curl -sS -m 20 "${TUNNEL}/api/health" || true
echo
echo "=== CORS headers ==="
curl -sS -m 20 -D- -o /dev/null -H "Origin: https://beresnevro-eng.github.io" "${TUNNEL}/api/health" | head -40 || true
echo "=== OPTIONS preflight ==="
curl -sS -m 20 -D- -o /dev/null -X OPTIONS "${TUNNEL}/api/auth" \
  -H "Origin: https://beresnevro-eng.github.io" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type,x-telegram-init-data" | head -40 || true

echo "DONE tunnel=$TUNNEL"
