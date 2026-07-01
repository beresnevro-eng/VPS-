#!/usr/bin/env bash
# VPS Autopilot — самовосстановление Fornex VPN (лёгкий, для 1 GB RAM)
# Cron: */15 * * * * root /root/vps-autopilot.sh >>/var/log/vps-autopilot.log 2>&1
set -uo pipefail

LOG=/var/log/vps-autopilot.log
STATE=/root/daily-telegram-security/.autopilot.state
ALERT_COOLDOWN=21600  # 6 ч между одинаковыми алертами

BOT_DIR=/root/daily-telegram-security
MIRROR_DIR="$BOT_DIR/mirrors"
BACKUP_GLOB=/root/backups/fornex-vps-*.tar.gz

XRAY_MAIN=/usr/local/etc/xray/config.json
XRAY_MIRROR=$BOT_DIR/xray-config.json
HY2_MAIN=/etc/hysteria/config.yaml
HY2_MIRROR=$MIRROR_DIR/hysteria-config.yaml
BOT_ENV=$BOT_DIR/config.env
BOT_ENV_MIRROR=$MIRROR_DIR/config.env
BOT_MAIN=$BOT_DIR/bot_poller.py

FIXES=()

log() { echo "$(date -Is) $*"; }

mark_fix() { FIXES+=("$1"); }

load_env() {
  TOKEN=""; CHAT=""
  [[ -f "$BOT_ENV" ]] || return 0
  TOKEN=$(grep -m1 '^TG_BOT_TOKEN=' "$BOT_ENV" 2>/dev/null | cut -d= -f2- | tr -d '"')
  CHAT=$(grep -m1 '^TG_CHAT_ID=' "$BOT_ENV" 2>/dev/null | cut -d= -f2- | tr -d '"')
  [[ -z "$CHAT" && -f "$BOT_DIR/tg_chat_id" ]] && CHAT=$(tr -d '[:space:]' <"$BOT_DIR/tg_chat_id")
}

send_alert() {
  local msg="$1"
  load_env
  [[ -n "$TOKEN" && -n "$CHAT" ]] || return 0
  local now hash last
  now=$(date +%s)
  hash=$(echo "$msg" | md5sum 2>/dev/null | awk '{print $1}' || echo "$msg")
  last=0
  [[ -f "$STATE" ]] && source "$STATE" 2>/dev/null || true
  if [[ "${LAST_HASH:-}" == "$hash" && $((now - ${LAST_TS:-0})) -lt $ALERT_COOLDOWN ]]; then
    return 0
  fi
  curl -fsS -m 20 -G "https://api.telegram.org/bot${TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${CHAT}" \
    --data-urlencode "text=${msg}" >/dev/null 2>&1 || true
  echo "LAST_TS=$now" >"$STATE"
  echo "LAST_HASH=$hash" >>"$STATE"
}

ensure_dns() {
  getent hosts api.telegram.org >/dev/null 2>&1 && return 0
  log "DNS down — fixing"
  if [[ -x /root/fix-dns-now.sh ]]; then
    /root/fix-dns-now.sh >/dev/null 2>&1 || true
  else
    cat > /etc/resolv.conf <<'EOF'
nameserver 1.1.1.1
nameserver 8.8.8.8
EOF
  fi
  if getent hosts api.telegram.org >/dev/null 2>&1; then
    mark_fix "DNS восстановлен"
  else
    mark_fix "DNS не поднялся"
  fi
}

restore_from_mirror() {
  local main=$1 mirror=$2 mode=${3:-644} owner=${4:-}
  [[ -f "$mirror" ]] || return 1
  if [[ -f "$main" ]]; then
    return 0
  fi
  cp "$mirror" "$main"
  chmod "$mode" "$main"
  if [[ -n "$owner" ]]; then
    chown "$owner" "$main" 2>/dev/null || true
  fi
  mark_fix "восстановлен $(basename "$main")"
  return 0
}

restore_from_latest_backup() {
  local path_in_tar=$1 dest=$2
  local arc
  arc=$(ls -1t $BACKUP_GLOB 2>/dev/null | head -1 || true)
  [[ -n "$arc" ]] || return 1
  tar -xzf "$arc" -C /tmp "./${path_in_tar}" 2>/dev/null || return 1
  cp "/tmp/${path_in_tar}" "$dest"
  mark_fix "восстановлен $(basename "$dest") из бэкапа"
}

sync_mirror() {
  local main=$1 mirror=$2
  [[ -f "$main" ]] || return 0
  if ! cmp -s "$main" "$mirror" 2>/dev/null; then
    cp "$main" "$mirror"
    chmod 600 "$mirror" 2>/dev/null || true
  fi
}

ensure_xray_config() {
  if [[ -f "$XRAY_MAIN" ]] && /usr/local/bin/xray -test -config "$XRAY_MAIN" >/dev/null 2>&1; then
    sync_mirror "$XRAY_MAIN" "$XRAY_MIRROR"
    return 0
  fi
  restore_from_mirror "$XRAY_MAIN" "$XRAY_MIRROR" 644 nobody:nogroup \
    || restore_from_latest_backup "daily-telegram-security/xray-config.json" "$XRAY_MIRROR" || true
  if [[ -f "$XRAY_MIRROR" ]]; then
    cp "$XRAY_MIRROR" "$XRAY_MAIN"
    chown nobody:nogroup "$XRAY_MAIN" 2>/dev/null || true
    chmod 644 "$XRAY_MAIN"
    if /usr/local/bin/xray -test -config "$XRAY_MAIN" >/dev/null 2>&1; then
      systemctl restart xray 2>/dev/null || true
      mark_fix "Xray config восстановлен"
    fi
  fi
}

ensure_hysteria_config() {
  restore_from_mirror "$HY2_MAIN" "$HY2_MIRROR" 600 \
    || restore_from_latest_backup "etc/hysteria/config.yaml" "$HY2_MAIN" || true
  sync_mirror "$HY2_MAIN" "$HY2_MIRROR"
}

ensure_bot_env() {
  restore_from_mirror "$BOT_ENV" "$BOT_ENV_MIRROR" 600 \
    || restore_from_latest_backup "daily-telegram-security/config.env" "$BOT_ENV" || true
  sync_mirror "$BOT_ENV" "$BOT_ENV_MIRROR"
}

ensure_bot_code() {
  [[ -f "$BOT_MAIN" ]] && return 0
  restore_from_latest_backup "daily-telegram-security/bot_poller.py" "$BOT_MAIN" || true
  for f in vpn_manager.py clients.py access_control.py sub_server.py report.py; do
    [[ -f "$BOT_DIR/$f" ]] && continue
    restore_from_latest_backup "daily-telegram-security/$f" "$BOT_DIR/$f" || true
  done
}

ensure_process() {
  local pattern=$1 unit=$2 label=$3
  if pgrep -f "$pattern" >/dev/null 2>&1; then
    return 0
  fi
  log "$label not running — starting"
  systemctl start "$unit" 2>/dev/null || true
  sleep 2
  if pgrep -f "$pattern" >/dev/null 2>&1; then
    mark_fix "$label запущен"
    return 0
  fi
  if [[ "$unit" == "xray-telegram-bot" && -x "$BOT_DIR/restart-bot.sh" ]]; then
    bash "$BOT_DIR/restart-bot.sh" >/dev/null 2>&1 || true
    pgrep -f "$pattern" >/dev/null 2>&1 && mark_fix "$label запущен (restart-bot)"
  fi
}

normalize_bot_processes() {
  local count
  count=$(pgrep -fc 'python3.*bot_poller.py' 2>/dev/null || echo 0)
  if [[ "$count" -gt 1 ]]; then
    pkill -f 'python3.*bot_poller.py' 2>/dev/null || true
    sleep 2
    ensure_process 'bot_poller.py' xray-telegram-bot 'Telegram-бот'
    mark_fix "лишние процессы бота убраны"
  fi
}

disk_maintenance() {
  local pct
  pct=$(df / --output=pcent | tail -1 | tr -dc '0-9')
  [[ "$pct" -ge 80 ]] || return 0
  apt-get clean -y 2>/dev/null || true
  journalctl --vacuum-time=14d 2>/dev/null || true
  find /var/log/xray -name 'access.log' -size +50M -exec truncate -s 0 {} \; 2>/dev/null || true
  if [[ -f /root/bot_poller.log ]]; then
    local sz
    sz=$(stat -c%s /root/bot_poller.log 2>/dev/null || echo 0)
    if [[ "$sz" -gt 52428800 ]]; then
      tail -5000 /root/bot_poller.log > /root/bot_poller.log.tmp
      mv /root/bot_poller.log.tmp /root/bot_poller.log
    fi
  fi
  mark_fix "очистка диска (было ${pct}%)"
}

main() {
  log "=== autopilot run ==="
  ensure_dns
  ensure_xray_config
  ensure_hysteria_config
  ensure_bot_env
  ensure_bot_code
  ensure_process '/usr/local/bin/xray run' xray 'Xray'
  ensure_process 'hysteria server' hysteria-server 'Hysteria2'
  ensure_process 'bot_poller.py' xray-telegram-bot 'Telegram-бот'
  normalize_bot_processes
  disk_maintenance

  if [[ ${#FIXES[@]} -gt 0 ]]; then
    local msg="🤖 Autopilot $(date +%H:%M) — исправлено:"
    for f in "${FIXES[@]}"; do msg+=$'\n'"• $f"; done
    log "$msg"
    send_alert "$msg"
  else
    log "OK — без изменений"
  fi
}

main "$@"
