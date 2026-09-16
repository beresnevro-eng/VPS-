#!/bin/bash
# Запуск из SSH-терминала VPS (не из Cursor sandbox).
set -euo pipefail
cd /root/couple-quiz-bot
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
unset SOCKS_PROXY SOCKS5_PROXY socks_proxy socks5_proxy
export NO_PROXY='*' no_proxy='*'

echo "=== 1) bot+API ==="
# аккуратные kill
for pid in $(pgrep -f '/root/couple-quiz-bot/\.venv/bin/python' 2>/dev/null || true); do
  cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
  case "$cmd" in /root/couple-quiz-bot/.venv/bin/python*) kill -9 "$pid" 2>/dev/null || true ;; esac
done
for pid in $(pgrep -x cloudflared 2>/dev/null || true); do kill -9 "$pid" 2>/dev/null || true; done
fuser -k 8080/tcp 2>/dev/null || true
sleep 1
bash /root/couple-quiz-bot/start.sh

echo "=== 2) wait health ==="
for i in $(seq 1 30); do
  if curl -sS -m 2 --noproxy '*' http://127.0.0.1:8080/api/health | grep -q ok; then
    echo "OK $(curl -sS --noproxy '*' http://127.0.0.1:8080/api/health)"
    break
  fi
  sleep 1
done

echo "=== 3) cloudflared ==="
rm -f /tmp/cloudflared.log /tmp/shepot_tunnel_url.txt
nohup /usr/local/bin/cloudflared tunnel --url http://127.0.0.1:8080 >/tmp/cloudflared.log 2>&1 &
echo "cfpid=$!"

TUNNEL=""
for i in $(seq 1 45); do
  TUNNEL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/cloudflared.log 2>/dev/null | grep -v 'api.trycloudflare.com' | head -1 || true)
  if [ -n "$TUNNEL" ]; then break; fi
  sleep 1
done
if [ -z "$TUNNEL" ]; then
  echo "FAIL tunnel"; tail -40 /tmp/cloudflared.log; exit 1
fi
echo "TUNNEL=$TUNNEL"
echo "$TUNNEL" >/tmp/shepot_tunnel_url.txt

python3 - <<PY
from pathlib import Path
import re
tunnel = open("/tmp/shepot_tunnel_url.txt").read().strip()
p = Path("/root/couple-quiz-bot/docs/app.js")
text = p.read_text()
text2, n = re.subn(r"https://[a-z0-9.-]+\.trycloudflare\.com", tunnel, text, count=1)
if not n:
    raise SystemExit("patch failed")
p.write_text(text2)
print("patched app.js")
PY

echo "=== 4) checks ==="
curl -sS -m 15 "$TUNNEL/api/health"; echo
curl -sS -m 15 -D- -o /dev/null -H "Origin: https://beresnevro-eng.github.io" "$TUNNEL/api/health" | tr -d '\r' | grep -iE 'HTTP/|access-control|content-type' || true

echo "=== 5) git push frontend ==="
cd /root/couple-quiz-bot
PROXY_HOSTPORT=$(echo "${HTTPS_PROXY:-${HTTP_PROXY:-}}" | sed -E 's#^https?://##')
if [ -n "${HTTPS_PROXY:-}${HTTP_PROXY:-}" ] && [ -n "$PROXY_HOSTPORT" ]; then
  export GIT_SSH_COMMAND="ssh -F /dev/null -i /root/.ssh/id_ed25519_github -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ProxyCommand=\"nc -X connect -x ${PROXY_HOSTPORT} %h %p\""
else
  export GIT_SSH_COMMAND="ssh -F /dev/null -i /root/.ssh/id_ed25519_github -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi
git add docs/ api.py main.py start.sh fix-miniapp.sh HOST_FIX_MINIAPP.sh 2>/dev/null || git add docs/ api.py main.py start.sh
export GIT_AUTHOR_NAME=beresnevro-eng GIT_AUTHOR_EMAIL=beresnevro-eng@users.noreply.github.com
export GIT_COMMITTER_NAME=$GIT_AUTHOR_NAME GIT_COMMITTER_EMAIL=$GIT_AUTHOR_EMAIL
git commit -m "Mini App: фикс CORS, диагностика, базовый UI" || echo "no commit needed"
git push origin HEAD:couple-quiz-bot || true

# gh-pages static
rm -rf /tmp/shepot-gh-pages && mkdir -p /tmp/shepot-gh-pages
cp docs/index.html docs/app.js docs/style.css /tmp/shepot-gh-pages/
cd /tmp/shepot-gh-pages
git init -b gh-pages >/dev/null
git add .
git -c user.name=beresnevro-eng -c user.email=beresnevro-eng@users.noreply.github.com commit -m "Mini App: фикс CORS, диагностика, базовый UI" >/dev/null
git remote add origin git@github.com:beresnevro-eng/VPS-.git
git push -f origin gh-pages

echo
echo "READY"
echo "Mini App: https://beresnevro-eng.github.io/VPS-/"
echo "API tunnel: $TUNNEL"
echo "Bot must keep polling — check: tail -f /root/couple-quiz-bot/bot.log"
