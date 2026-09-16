#!/bin/bash
# Диагностика сети для Telegram-бота + запуск
set -euo pipefail

echo "=== DNS ==="
cat /etc/resolv.conf
getent hosts api.telegram.org || true
dig +short api.telegram.org @8.8.8.8 || true
dig +short api.telegram.org @127.0.0.53 || true

echo "=== HTTPS direct ==="
curl -4 -sS --noproxy '*' --connect-timeout 10 -o /dev/null -w "direct:%{http_code}\n" https://api.telegram.org || echo "direct:FAIL"

echo "=== DoH (если UDP DNS мёртв) ==="
DOH_JSON=$(curl -4 -sS --noproxy '*' --connect-timeout 10 \
  'https://cloudflare-dns.com/dns-query?name=api.telegram.org&type=A' \
  -H 'accept: application/dns-json' || true)
echo "$DOH_JSON" | head -c 400; echo
IP=$(python3 - <<'PY' "$DOH_JSON"
import json,sys
raw=sys.argv[1] if len(sys.argv)>1 else ""
try:
    data=json.loads(raw)
    for a in data.get("Answer",[]):
        if a.get("type")==1:
            print(a["data"]); break
except Exception:
    pass
PY
)
if [[ -n "${IP:-}" ]]; then
  echo "Resolved via DoH: $IP"
  grep -q 'api.telegram.org' /etc/hosts && sed -i '/api.telegram.org/d' /etc/hosts
  echo "$IP api.telegram.org" >> /etc/hosts
  echo "Added to /etc/hosts"
fi

echo "=== Local SOCKS ==="
ss -tulnp | grep -E '1080|1081|8443' || true
curl -4 -sS --connect-timeout 10 --socks5-hostname 127.0.0.1:1080 -o /dev/null -w "dante1080:%{http_code}\n" https://api.telegram.org || echo "dante1080:FAIL"
curl -4 -sS --connect-timeout 10 --socks5-hostname 127.0.0.1:8443 -o /dev/null -w "xray8443:%{http_code}\n" https://api.telegram.org || echo "xray8443:FAIL"

echo "=== getMe ==="
# shellcheck disable=SC1091
set -a; source /root/couple-quiz-bot/.env; set +a
curl -4 -sS --noproxy '*' --connect-timeout 15 \
  "https://api.telegram.org/bot${BOT_TOKEN}/getMe" || echo "getMe FAIL"

echo "=== restart bot ==="
bash /root/couple-quiz-bot/start.sh
