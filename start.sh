#!/bin/bash
# Гарантированно один экземпляр couple-quiz-bot
set -euo pipefail
cd /root/couple-quiz-bot

unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
unset SOCKS_PROXY SOCKS5_PROXY socks_proxy socks5_proxy
export NO_PROXY='*'
export no_proxy='*'

echo "=== Останавливаю ВСЕ копии бота ==="
# Важно: в ps путь часто просто ".venv/bin/python main.py" (без couple-quiz-bot)
kill_bots() {
  pgrep -f '/root/couple-quiz-bot/.venv/bin/python' 2>/dev/null || true
  pgrep -f 'couple-quiz-bot/.venv/bin/python' 2>/dev/null || true
  # процессы, у которых cwd = project (через /proc)
  for pid in $(pgrep -f 'python.*main\.py' 2>/dev/null || true); do
    cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)
    if [[ "$cwd" == "/root/couple-quiz-bot" ]]; then
      echo "$pid"
    fi
  done
}

for _ in 1 2 3 4 5; do
  mapfile -t pids < <(kill_bots | sort -u)
  if [[ ${#pids[@]} -eq 0 || -z "${pids[0]:-}" ]]; then
    break
  fi
  echo "kill: ${pids[*]}"
  kill "${pids[@]}" 2>/dev/null || true
  sleep 1
  kill -9 "${pids[@]}" 2>/dev/null || true
  sleep 1
done

left=$(kill_bots | sort -u || true)
if [[ -n "${left}" ]]; then
  echo "WARN: ещё живы: $left"
  kill -9 $left 2>/dev/null || true
  sleep 1
fi
echo "OK: проверка процессов:"
kill_bots | sort -u || echo "(пусто — хорошо)"

set -a
# shellcheck disable=SC1091
source /root/couple-quiz-bot/.env
set +a

echo "=== deleteWebhook ==="
curl -4 -sS --noproxy '*' --connect-timeout 15 \
  "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook?drop_pending_updates=true" || true
echo

# IPv4 hosts (если есть права)
if [[ -w /etc/hosts ]]; then
  sed -i '/[[:space:]]api\.telegram\.org$/d' /etc/hosts 2>/dev/null || true
  TG_IP="149.154.167.220"
  for ip in 149.154.167.220 149.154.167.222 149.154.175.100 149.154.175.50; do
    if timeout 2 bash -c "echo >/dev/tcp/${ip}/443" 2>/dev/null; then
      TG_IP=$ip
      break
    fi
  done
  echo "$TG_IP api.telegram.org" >> /etc/hosts || true
  echo "resolve: $(getent ahostsv4 api.telegram.org | head -1 || true)"
fi

# Модель Groq: не перезаписываем .env устаревшими id.
# Актуальные free/dev (после 16.08.2026): openai/gpt-oss-20b, openai/gpt-oss-120b, qwen/qwen3.6-27b
if ! grep -q '^GROQ_MODEL=' .env 2>/dev/null; then
  echo 'GROQ_MODEL=openai/gpt-oss-20b' >> .env
fi
if ! grep -q '^GROQ_MODEL_FALLBACKS=' .env 2>/dev/null; then
  echo 'GROQ_MODEL_FALLBACKS=openai/gpt-oss-120b,qwen/qwen3.6-27b' >> .env
fi

: > bot.log
nohup env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  NO_PROXY='*' no_proxy='*' \
  /root/couple-quiz-bot/.venv/bin/python -u /root/couple-quiz-bot/main.py >> /root/couple-quiz-bot/bot.log 2>&1 &
NEWPID=$!
echo "started pid=$NEWPID"
echo "GROQ_MODEL=$(grep '^GROQ_MODEL=' .env | head -1)"
sleep 5

echo "=== процессы сейчас (должен быть РОВНО 1) ==="
kill_bots | sort -u
count=$(kill_bots | sort -u | grep -c . || true)
echo "count=$count"
echo "=== хвост лога ==="
tail -n 25 bot.log
