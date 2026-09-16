#!/bin/bash
# Безопасный фикс Mini App. НЕ трогает порт 8080 (там Xray — ваш VPN/SSH).
set -euo pipefail
cd /root/couple-quiz-bot

unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
unset SOCKS_PROXY SOCKS5_PROXY socks_proxy socks5_proxy
export NO_PROXY='*' no_proxy='*'

# Гарантируем порт API != 8080
if grep -q '^API_PORT=' .env; then
  sed -i 's/^API_PORT=.*/API_PORT=8787/' .env
else
  echo 'API_PORT=8787' >> .env
fi
API_PORT=8787
echo "API_PORT=$API_PORT (Xray остаётся на 8080)"

echo "=== 1) restart bot+API (only couple-quiz-bot) ==="
for pid in $(pgrep -f '/root/couple-quiz-bot/\.venv/bin/python' 2>/dev/null || true); do
  cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
  case "$cmd" in
    /root/couple-quiz-bot/.venv/bin/python*) kill "$pid" 2>/dev/null || true ;;
  esac
done
sleep 2
# Только cloudflared нашего туннеля — не трогаем xray/hysteria/sshd
for pid in $(pgrep -x cloudflared 2>/dev/null || true); do
  cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
  case "$cmd" in
    *'tunnel --url http://127.0.0.1:'*) kill "$pid" 2>/dev/null || true ;;
  esac
done
sleep 1

bash /root/couple-quiz-bot/start.sh

echo "=== 2) wait health on :$API_PORT ==="
ok=0
for i in $(seq 1 25); do
  body=$(curl -sS -m 2 --noproxy '*' --http1.1 "http://127.0.0.1:${API_PORT}/api/health" 2>/dev/null || true)
  if echo "$body" | grep -q '"status"'; then
    echo "OK $body"
    ok=1
    break
  fi
  sleep 1
done
if [ "$ok" != 1 ]; then
  echo "FAIL local health"; tail -n 40 bot.log; exit 1
fi

echo "=== 3) cloudflared -> :$API_PORT ==="
rm -f /tmp/cloudflared.log /tmp/shepot_tunnel_url.txt
nohup /usr/local/bin/cloudflared tunnel --url "http://127.0.0.1:${API_PORT}" >/tmp/cloudflared.log 2>&1 &
echo "cfpid=$!"

TUNNEL=""
for i in $(seq 1 45); do
  TUNNEL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/cloudflared.log 2>/dev/null \
    | grep -v 'api.trycloudflare.com' | head -1 || true)
  if [ -n "$TUNNEL" ]; then break; fi
  sleep 1
done
if [ -z "$TUNNEL" ]; then
  echo "FAIL tunnel"; tail -n 40 /tmp/cloudflared.log; exit 1
fi
echo "TUNNEL=$TUNNEL"
echo "$TUNNEL" >/tmp/shepot_tunnel_url.txt

python3 - <<'PY'
from pathlib import Path
import re
tunnel = open("/tmp/shepot_tunnel_url.txt").read().strip()
p = Path("/root/couple-quiz-bot/docs/app.js")
text = p.read_text()
text2, n = re.subn(r"https://[a-z0-9.-]+\.trycloudflare\.com", tunnel, text, count=1)
if n == 0:
    # fallback: replace placeholder assignment value
    text2, n = re.subn(
        r'(window\.SHEPOT_API_BASE\s*=\s*\n?\s*window\.SHEPOT_API_BASE\s*\|\|\s*")[^"]+(")',
        rf"\1{tunnel}\2",
        text,
        count=1,
    )
if n == 0:
    raise SystemExit("patch failed")
p.write_text(text2)
print("patched app.js ->", tunnel)
PY

echo "=== 4) checks ==="
curl -sS -m 15 --http1.1 "${TUNNEL}/api/health"; echo
curl -sS -m 15 --http1.1 -D- -o /dev/null -H "Origin: https://beresnevro-eng.github.io" \
  "${TUNNEL}/api/health" | tr -d '\r' | grep -iE 'HTTP/|access-control|content-type' || true

echo "=== 5) git push gh-pages ==="
cd /root/couple-quiz-bot
export GIT_SSH_COMMAND="ssh -F /dev/null -i /root/.ssh/id_ed25519_github -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
export GIT_AUTHOR_NAME=beresnevro-eng GIT_AUTHOR_EMAIL=beresnevro-eng@users.noreply.github.com
export GIT_COMMITTER_NAME=$GIT_AUTHOR_NAME GIT_COMMITTER_EMAIL=$GIT_AUTHOR_EMAIL
git add docs/ api.py main.py start.sh HOST_FIX_MINIAPP.sh
git commit -m "Mini App: API на :8787 (не трогаем Xray :8080)" || echo "no commit"
git push origin HEAD:couple-quiz-bot || true

rm -rf /tmp/shepot-gh-pages && mkdir -p /tmp/shepot-gh-pages
cp docs/index.html docs/app.js docs/style.css /tmp/shepot-gh-pages/
cd /tmp/shepot-gh-pages
git init -b gh-pages >/dev/null
git add .
git -c user.name=beresnevro-eng -c user.email=beresnevro-eng@users.noreply.github.com \
  commit -m "Mini App: API tunnel update" >/dev/null
git remote add origin git@github.com:beresnevro-eng/VPS-.git
git push -f origin gh-pages

echo
echo "READY — SSH/VPN не должны обрываться"
echo "Mini App: https://beresnevro-eng.github.io/VPS-/"
echo "API tunnel: $TUNNEL"
echo "Local API:  http://127.0.0.1:${API_PORT}/api/health"
echo "Xray :8080 не трогали"
