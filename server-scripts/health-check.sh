#!/bin/bash
set -uo pipefail

ISSUES=()

check_service() {
  local name=$1
  if systemctl is-active --quiet "$name" 2>/dev/null; then
    echo "OK  $name"
    return
  fi
  case "$name" in
    xray) pgrep -f '/usr/local/bin/xray run' >/dev/null 2>&1 && { echo "OK  $name (process)"; return; } ;;
    hysteria-server) pgrep -f 'hysteria server' >/dev/null 2>&1 && { echo "OK  $name (process)"; return; } ;;
    xray-telegram-bot) pgrep -f 'bot_poller.py' >/dev/null 2>&1 && { echo "OK  $name (process)"; return; } ;;
    awg-quick@*) ip link show awg0 &>/dev/null && awg show awg0 &>/dev/null && { echo "OK  $name (interface)"; return; } ;;
  esac
  ISSUES+=("$name не запущен")
  echo "FAIL $name"
}

check_file() {
  local path=$1 desc=$2
  if [[ -f "$path" ]]; then
    echo "OK  $desc"
  else
    ISSUES+=("$desc отсутствует: $path")
    echo "FAIL $desc ($path)"
  fi
}

echo "=== Health check $(date -Is) ==="

check_service xray
check_service xray-telegram-bot
check_service hysteria-server

if [[ -f /etc/amnezia/amneziawg/awg0.conf ]] || [[ -f /etc/wireguard/awg0.conf ]]; then
  check_service awg-quick@awg0
fi

if [[ -f /etc/wireguard/wg-hop.conf ]]; then
  if systemctl is-active --quiet wg-quick@wg-hop 2>/dev/null || ip link show wg-hop &>/dev/null; then
    echo "OK  wg-quick@wg-hop"
  else
    ISSUES+=("wg-hop не запущен")
    echo "FAIL wg-quick@wg-hop"
  fi
fi

if dpkg -s fail2ban &>/dev/null; then
  check_service fail2ban
fi

check_file /usr/local/etc/xray/config.json "Xray config"
check_file /root/daily-telegram-security/xray-config.json "Xray mirror"
check_file /etc/hysteria/config.yaml "Hysteria config"
check_file /root/daily-telegram-security/config.env "Bot secrets"

BOT_COUNT=$(pgrep -fc 'python3.*bot_poller.py' 2>/dev/null || echo 0)
if [[ "$BOT_COUNT" -eq 1 ]]; then echo "OK  bot process (1)"
elif [[ "$BOT_COUNT" -eq 0 ]]; then ISSUES+=("бот не запущен"); echo "FAIL bot process (0)"
else ISSUES+=("несколько процессов бота: $BOT_COUNT"); echo "WARN bot process ($BOT_COUNT)"; fi

DISK_PCT=$(df / --output=pcent | tail -1 | tr -dc '0-9')
if [[ "$DISK_PCT" -ge 90 ]]; then ISSUES+=("диск ${DISK_PCT}%"); echo "FAIL disk ${DISK_PCT}%"
elif [[ "$DISK_PCT" -ge 80 ]]; then echo "WARN disk ${DISK_PCT}%"
else echo "OK  disk ${DISK_PCT}%"; fi

if getent hosts api.telegram.org >/dev/null 2>&1; then echo "OK  DNS"
else ISSUES+=("DNS не работает"); echo "FAIL DNS"; fi

TOKEN=$(grep -m1 '^TG_BOT_TOKEN=' /root/daily-telegram-security/config.env 2>/dev/null | cut -d= -f2 | tr -d '"')
CHAT=$(grep -m1 '^TG_CHAT_ID=' /root/daily-telegram-security/config.env 2>/dev/null | cut -d= -f2 | tr -d '"')
if [[ -n "$TOKEN" ]] && curl -fsS -m 15 "https://api.telegram.org/bot${TOKEN}/getMe" | grep -q '"ok":true'; then
  echo "OK  Telegram API"
else ISSUES+=("Telegram API недоступен"); echo "FAIL Telegram API"; fi

# --- Шёпот Mini App ---
SHEPOT_API=$(pgrep -fc '/root/couple-quiz-bot/.venv/bin/python' 2>/dev/null || echo 0)
if [[ "$SHEPOT_API" -ge 1 ]]; then echo "OK  shepot bot process ($SHEPOT_API)"
else ISSUES+=("Шёпот: бот не запущен"); echo "FAIL shepot bot"; fi

if curl -fsS -m 3 --noproxy '*' http://127.0.0.1:8787/api/health 2>/dev/null | grep -q '"status"'; then
  echo "OK  shepot API :8787"
else ISSUES+=("Шёпот: API :8787 не отвечает"); echo "FAIL shepot API"; fi

if pgrep -x cloudflared >/dev/null 2>&1; then echo "OK  cloudflared"
else ISSUES+=("Шёпот: cloudflared не запущен (Mini App недоступен)"); echo "FAIL cloudflared"; fi

if curl -fsS -m 12 https://app.wspr.online/api/health 2>/dev/null | grep -q '"status"'; then
  echo "OK  app.wspr.online"
else ISSUES+=("Шёпот: app.wspr.online недоступен"); echo "FAIL app.wspr.online"; fi

# --- RU entry VPS (RuVDS active + RuVDS-old standby) ---
probe_tcp() {
  local host=$1 port=$2
  timeout 3 bash -c "echo >/dev/tcp/${host}/${port}" 2>/dev/null
}

check_ru_entry() {
  local name=$1 ip=$2 role=$3
  local fail=0
  if probe_tcp "$ip" 22; then
    echo "OK  $name SSH :22 ($ip, $role)"
  else
    ISSUES+=("$name: SSH :22 недоступен ($ip)")
    echo "FAIL $name SSH :22"
    fail=1
  fi
  if probe_tcp "$ip" 443; then
    echo "OK  $name VLESS :443 ($ip, $role)"
  else
    ISSUES+=("$name: :443 недоступен ($ip)")
    echo "FAIL $name VLESS :443"
    fail=1
  fi
  return "$fail"
}

echo "--- RU entries ---"
check_ru_entry "RuVDS" "195.133.146.140" "active" || true
check_ru_entry "RuVDS-old" "194.226.54.124" "standby" || true

if [[ -f /etc/wireguard/wg-hop.conf ]] || ip link show wg-hop &>/dev/null; then
  echo "--- hop ---"
  if ip link show wg-hop &>/dev/null; then
    echo "OK  wg-hop interface"
  else
    ISSUES+=("wg-hop интерфейс отсутствует"); echo "FAIL wg-hop interface"
  fi
  if ping -c1 -W2 10.99.0.1 >/dev/null 2>&1; then
    echo "OK  ping hop peer 10.99.0.1"
  else
    ISSUES+=("hop: нет ping 10.99.0.1"); echo "FAIL ping 10.99.0.1"
  fi
  HS=$(wg show wg-hop latest-handshakes 2>/dev/null | awk 'NR==1 && $2+0>0 {print systime()-$2}')
  if [[ -n "${HS:-}" ]]; then
    if [[ "$HS" -le 180 ]]; then echo "OK  hop handshake ${HS}s ago"
    else ISSUES+=("hop handshake устарел: ${HS}s"); echo "FAIL hop handshake ${HS}s"
    fi
  else
    ISSUES+=("hop: нет handshake"); echo "FAIL hop handshake"
  fi
  EXIT_IP=$(curl -sS --max-time 8 --socks5-hostname 10.99.0.2:11081 https://ifconfig.me 2>/dev/null || true)
  if [[ "$EXIT_IP" == "103.75.126.216" ]]; then
    echo "OK  hop SOCKS exit $EXIT_IP"
  else
    ISSUES+=("hop SOCKS exit: ${EXIT_IP:-timeout}")
    echo "FAIL hop SOCKS (got '${EXIT_IP:-timeout}')"
  fi
fi

if [[ ${#ISSUES[@]} -gt 0 ]]; then
  MSG="⚠️ Health-check $(date +%H:%M):"
  for issue in "${ISSUES[@]}"; do
    MSG+=$'\n'"${issue}"
  done
  if [[ -n "$TOKEN" && -n "$CHAT" ]]; then
    curl -fsS -m 20 -G "https://api.telegram.org/bot${TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${CHAT}" \
      --data-urlencode "text=${MSG}" >/dev/null 2>&1 || true
  fi
  echo "RESULT: ${#ISSUES[@]} issue(s)"
  exit 1
fi
echo "RESULT: all OK"
