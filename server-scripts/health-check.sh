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
