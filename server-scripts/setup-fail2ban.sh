#!/usr/bin/env bash
# Установка и настройка fail2ban для SSH (идемпотентно)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JAIL_DST=/etc/fail2ban/jail.d/fornex-sshd.local
JAIL_SRC=""
for candidate in \
  "$SCRIPT_DIR/fail2ban/jail.d/fornex-sshd.local" \
  "$SCRIPT_DIR/server-scripts/fail2ban/jail.d/fornex-sshd.local" \
  "/root/daily-telegram-security/server-scripts/fail2ban/jail.d/fornex-sshd.local"; do
  if [[ -f "$candidate" ]]; then
    JAIL_SRC="$candidate"
    break
  fi
done

log() { echo "[fail2ban] $*"; }

if [[ -z "$JAIL_SRC" ]]; then
  echo "Не найден fornex-sshd.local (искали рядом со скриптом и в server-scripts/)" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
if ! dpkg -s fail2ban &>/dev/null; then
  log "установка пакета..."
  apt-get update -qq
  apt-get install -y -qq fail2ban
else
  log "пакет уже установлен"
fi

install -m 644 "$JAIL_SRC" "$JAIL_DST"
log "конфиг: $JAIL_DST"

systemctl enable fail2ban >/dev/null 2>&1 || true
systemctl restart fail2ban

sleep 2
if ! systemctl is-active --quiet fail2ban; then
  echo "fail2ban не запустился" >&2
  systemctl status fail2ban --no-pager || true
  exit 1
fi

if ! fail2ban-client ping &>/dev/null; then
  echo "fail2ban-client ping failed" >&2
  exit 1
fi

fail2ban-client reload sshd >/dev/null 2>&1 || fail2ban-client reload >/dev/null 2>&1 || true

log "статус sshd jail:"
fail2ban-client status sshd 2>/dev/null || fail2ban-client status

log "готово"
