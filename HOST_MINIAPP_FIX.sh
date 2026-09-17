#!/bin/bash
set -euo pipefail
# Refuse to run inside Cursor agent sandbox (isolated netns / no caps)
if grep -q $'CapEff:\t0000000000000000' /proc/self/status 2>/dev/null; then
  echo "ERROR: HOST_MINIAPP_FIX.sh refused: running inside Cursor sandbox (CapEff=0)." >&2
  echo "Run this in the VPS IDE terminal (unsandboxed): bash /root/couple-quiz-bot/HOST_MINIAPP_FIX.sh" >&2
  exit 99
fi

# Prefer writing our own result file (also works when stdout is redirected by cron)
exec > /tmp/shepot-fix-result.txt 2>&1
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy \
  SOCKS_PROXY SOCKS5_PROXY socks_proxy socks5_proxy GIT_HTTP_PROXY GIT_HTTPS_PROXY || true
export NO_PROXY='*' no_proxy='*'
export HOME=/root
# Avoid broken macOS-only ssh config options on this Linux host
export GIT_SSH_COMMAND='ssh -F /dev/null -i /root/.ssh/id_ed25519_github -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new'

echo "=== START $(date -Is) CapEff=$(awk '/CapEff/{print $2}' /proc/self/status) ==="
cd /root/couple-quiz-bot

free_8080() {
  echo "=== listeners on :8080 ==="
  ss -lntp 2>/dev/null | grep -E ':8080\b' || netstat -lntp 2>/dev/null | grep 8080 || true
  # Kill anything bound to TCP 8080 (both 0.0.0.0 and 127.0.0.1)
  if command -v fuser >/dev/null 2>&1; then
    fuser -k 8080/tcp 2>/dev/null || true
  fi
  # Also by ss PIDs
  for pid in $(ss -lntp 2>/dev/null | awk '/:8080/{while (match($0,/pid=[0-9]+/)){print substr($0,RSTART+4,RLENGTH-4); $0=substr($0,RSTART+RLENGTH)}}'); do
    echo "kill 8080-holder pid=$pid"
    kill -9 "$pid" 2>/dev/null || true
  done
  sleep 1
  ss -lntp 2>/dev/null | grep -E ':8080\b' || echo "8080 free"
}

health_ok() {
  # Prefer HTTP/1.1; capture body
  curl --noproxy '*' --http1.1 -sS -m 5 "http://127.0.0.1:8080/health" \
    || curl --noproxy '*' --http1.1 -sS -m 5 "http://0.0.0.0:8080/health" \
    || return 1
}

# 1. Ensure API healthy
BODY=""
if BODY=$(health_ok); then
  echo "HEALTH=$BODY"
else
  echo "API down / bad response — freeing 8080 and restarting bot"
  free_8080
  bash /root/couple-quiz-bot/start.sh
  sleep 3
fi

# Restart cleanly once more if still unhealthy
if ! BODY=$(health_ok); then
  echo "Still unhealthy after start; force restart"
  free_8080
  bash /root/couple-quiz-bot/start.sh
  sleep 4
fi
if ! BODY=$(health_ok); then
  echo "HEALTH_FAIL after restart"
  ss -lntp | grep 8080 || true
  # dump who owns 8080
  fuser -v 8080/tcp 2>&1 || true
  tail -40 /root/couple-quiz-bot/bot.log || true
  exit 1
fi
echo "HEALTH=$BODY"
printf '%s' "$BODY" > /tmp/shepot-local-health.json

# 2. Restart cloudflared
mapfile -t CF_PIDS < <(pgrep -f '/usr/local/bin/cloudflared tunnel --url' || true)
for pid in "${CF_PIDS[@]:-}"; do
  [ -n "${pid:-}" ] || continue
  kill "$pid" 2>/dev/null || true
done
sleep 1
rm -f /tmp/cloudflared.log
nohup /usr/local/bin/cloudflared tunnel --url http://127.0.0.1:8080 > /tmp/cloudflared.log 2>&1 &
echo "cloudflared_pid=$!"

# 3. Parse URL
TUNNEL=""
for _ in $(seq 1 45); do
  TUNNEL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/cloudflared.log 2>/dev/null \
    | grep -v '://api\.trycloudflare\.com' | head -1 || true)
  if [ -n "$TUNNEL" ]; then break; fi
  sleep 1
done
echo "TUNNEL=$TUNNEL"
if [ -z "$TUNNEL" ]; then
  echo "FAILED to get tunnel URL"
  tail -80 /tmp/cloudflared.log || true
  exit 1
fi

# 4. Update app.js (repo + worktrees)
python3 - "$TUNNEL" <<'PY'
import re, sys
from pathlib import Path
url = sys.argv[1]
paths = [Path("/root/couple-quiz-bot/docs/app.js")]
paths += list(Path("/var/tmp/cursor-home/.cursor/worktrees").glob("couple-quiz-*/couple-quiz-bot-*/docs/app.js"))
for p in paths:
    if not p.exists():
        continue
    text = p.read_text()
    new = re.sub(
        r'window\.SHEPOT_API_BASE\s*=\s*["\'][^"\']*["\']',
        f'window.SHEPOT_API_BASE = "{url}"',
        text,
        count=1,
    )
    p.write_text(new)
    print("updated", p, "->", url)
PY

# 5. Push gh-pages
GH=/tmp/shepot-gh-pages
export GIT_SSH_COMMAND='ssh -F /dev/null -i /root/.ssh/id_ed25519_github -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new'
rm -rf "$GH"
git clone --branch gh-pages git@github.com:beresnevro-eng/VPS-.git "$GH"
cp -f /root/couple-quiz-bot/docs/* "$GH/" 2>/dev/null || true
cd "$GH"
git add -A
if ! git diff --cached --quiet; then
  git -c user.email=bot@local -c user.name=shepot-fix commit -m "Update SHEPOT_API_BASE to ${TUNNEL}"
  echo "=== GH-PAGES PUSH ==="
  git push origin gh-pages
else
  echo "gh-pages: no changes"
fi

# 6. Commit couple-quiz-bot branch if needed
cd /root/couple-quiz-bot
git add docs/app.js api.py main.py start.sh 2>/dev/null || true
if ! git diff --cached --quiet 2>/dev/null; then
  git -c user.email=bot@local -c user.name=shepot-fix commit -m "Fix Mini App API (/health) and tunnel base ${TUNNEL}"
  echo "=== COUPLE-QUIZ PUSH ==="
  git push origin HEAD:couple-quiz-bot 2>&1 || git push origin HEAD 2>&1 || true
else
  echo "couple-quiz-bot: no commit needed"
fi

# 7. Tests
echo "=== TUNNEL /health ==="
curl --noproxy '*' --http1.1 -sS -m 20 -D - "${TUNNEL}/health" -o /tmp/shepot-tunnel-health.body || true
echo
echo -n "BODY="; cat /tmp/shepot-tunnel-health.body; echo
echo "=== TUNNEL OPTIONS CORS ==="
curl --noproxy '*' --http1.1 -sS -m 20 -D - -o /dev/null -X OPTIONS "${TUNNEL}/health" \
  -H 'Origin: https://beresnevro-eng.github.io' \
  -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Headers: content-type' || true
echo "=== DONE $(date -Is) ==="
