#!/usr/bin/env bash
# Безопасный запуск AmneziaWG — НЕ правит /etc/ufw/before.rules
# Только: sysctl, ufw allow 51830/udp, awg-quick@awg0, iptables в PostUp конфига
set -euo pipefail

AWG_PORT=51830
CONF=/etc/amnezia/amneziawg/awg0.conf
CLIENTS=/root/amnesiawg-clients
BACKUP_DIR=/root/backups
LOG=/root/backups/awg-safe-start.log

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
die() { log "ERROR: $*"; exit 1; }

verify_xray() {
  systemctl is-active --quiet xray || die "Xray не active — AWG не трогаем"
  ss -tlnp 2>/dev/null | grep -q ':443 ' || die "Порт 443 не слушает — AWG не трогаем"
  log "OK: Xray :443"
}

detect_nic() {
  local nic
  nic=$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}')
  [[ -z "$nic" || "$nic" == "lo" ]] && nic=$(ip route get 8.8.8.8 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}')
  [[ -n "$nic" && "$nic" != "lo" ]] || nic=eth0
  echo "$nic"
}

rollback_awg() {
  log "ROLLBACK: останавливаю AWG"
  systemctl stop awg-quick@awg0 2>/dev/null || true
  systemctl disable awg-quick@awg0 2>/dev/null || true
}

trap 'if [[ $? -ne 0 ]]; then rollback_awg; verify_xray 2>/dev/null || true; fi' EXIT

[[ "$(id -u)" -eq 0 ]] || die "запускай от root"
[[ -f "$CONF" ]] || die "нет $CONF — сначала: bash /root/setup-amnesiawg-lite.sh"

log "=== AWG safe start ==="
verify_xray

mkdir -p "$BACKUP_DIR"
cp -a /etc/ufw/before.rules "$BACKUP_DIR/ufw-before.rules.bak.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
log "Бэкап before.rules в $BACKUP_DIR"

# Убрать битый I1 если остался
sed -i '/^I1[[:space:]]*=/d' "$CONF" "$CLIENTS"/*.conf 2>/dev/null || true

NIC=$(detect_nic)
log "NIC: $NIC"

# PostUp/PostDown: правила в ufw-before-forward (runtime), NAT — без правки файлов UFW
POSTUP="iptables -I ufw-before-forward 1 -i awg0 -j ACCEPT; iptables -I ufw-before-forward 1 -o awg0 -j ACCEPT; iptables -t nat -C POSTROUTING -s 10.66.66.0/24 -o ${NIC} -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s 10.66.66.0/24 -o ${NIC} -j MASQUERADE"
POSTDOWN="iptables -D ufw-before-forward -i awg0 -j ACCEPT 2>/dev/null || true; iptables -D ufw-before-forward -o awg0 -j ACCEPT 2>/dev/null || true; iptables -t nat -D POSTROUTING -s 10.66.66.0/24 -o ${NIC} -j MASQUERADE 2>/dev/null || true"

# Обновить PostUp/PostDown в конфиге (идемпотентно через перезапись строк)
python3 - <<PY
from pathlib import Path
import re
p = Path("$CONF")
text = p.read_text()
text = re.sub(r'^PostUp = .*$', 'PostUp = ${POSTUP}', text, flags=re.M)
text = re.sub(r'^PostDown = .*$', 'PostDown = ${POSTDOWN}', text, flags=re.M)
p.write_text(text)
PY

sysctl -p /etc/sysctl.d/99-amnesiawg-forwarding.conf 2>/dev/null || sysctl -w net.ipv4.ip_forward=1

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi active; then
  ufw allow "${AWG_PORT}/udp" comment "AmneziaWG" 2>/dev/null || true
  log "UFW: allow ${AWG_PORT}/udp (без reload, без before.rules)"
fi

systemctl enable awg-quick@awg0
systemctl restart awg-quick@awg0
sleep 2

systemctl is-active --quiet awg-quick@awg0 || {
  journalctl -u awg-quick@awg0 -n 15 --no-pager | tee -a "$LOG"
  die "awg-quick@awg0 не запустился"
}

awg show awg0 | tee -a "$LOG"
ss -ulnp | grep ":${AWG_PORT}" | tee -a "$LOG" || die "UDP ${AWG_PORT} не слушает"

verify_xray
log "=== AWG запущен, Xray цел ==="
log "Тест: /awg iphone в боте → импорт в AmneziaVPN"
log "Откат: bash /root/awg-safe-stop.sh"

trap - EXIT
