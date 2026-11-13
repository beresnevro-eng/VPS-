#!/bin/bash
set -uo pipefail
EXPECTED=103.75.126.216
LOG=/var/log/hop-watchdog.log
ts(){ date -Is; }
log(){ echo "$(ts) $*" | tee -a "$LOG"; }

check_socks() {
  local host=$1 port=$2
  local ip
  ip=$(curl -4 -sS --max-time 12 --socks5-hostname "${host}:${port}" https://ifconfig.me 2>/dev/null || true)
  [[ "$ip" == "$EXPECTED" ]]
}

fix=0

# WG hop
HS=$(wg show wg-hop latest-handshakes 2>/dev/null | awk 'NR==1{print $2}')
NOW=$(date +%s)
if [[ -z "${HS:-}" || "$HS" -eq 0 || $((NOW-HS)) -gt 180 ]]; then
  log "WARN wg handshake stale (${HS:-none}); restart wg-hop"
  systemctl restart wg-quick@wg-hop
  sleep 3
  fix=1
fi
if ! ping -c1 -W2 10.99.0.1 >/dev/null 2>&1; then
  log "WARN ping 10.99.0.1 fail; restart wg-hop"
  systemctl restart wg-quick@wg-hop
  sleep 3
  fix=1
fi

# Primary socks via WG
if ! check_socks 10.99.0.2 11081; then
  log "WARN socks 10.99.0.2:11081 bad; restart hop-socks/wg"
  systemctl restart hop-socks wg-quick@wg-hop
  sleep 2
  fix=1
fi

# Backup reverse SSH to old:11081
if ! check_socks 127.0.0.1 11082; then
  log "WARN local 11082 bad; restart hop-socks-local"
  systemctl restart hop-socks-local hop-socks
  sleep 2
  fix=1
fi
# Probe remote reverse port via SSH
if ! ssh -i /root/.ssh/id_hop_bridge -o BatchMode=yes -o ConnectTimeout=8 root@194.226.54.124 \
    'ss -tln | grep -q ":11081"' 2>/dev/null; then
  log "WARN old:11081 not listening; restart hop-bridge-ssh"
  systemctl restart hop-bridge-ssh
  sleep 4
  fix=1
fi
# End-to-end via reverse path (from old perspective)
if ! ssh -i /root/.ssh/id_hop_bridge -o BatchMode=yes -o ConnectTimeout=8 root@194.226.54.124 \
    "curl -4 -sS --max-time 12 --socks5-hostname 127.0.0.1:11081 https://ifconfig.me" 2>/dev/null \
    | grep -qx "$EXPECTED"; then
  log "WARN e2e via old:11081 fail; restart bridge+socks"
  systemctl restart hop-bridge-ssh hop-socks-local hop-socks
  sleep 4
  fix=1
fi

if [[ "$fix" -eq 0 ]]; then
  log "OK hop healthy"
else
  log "REPAIRED hop components"
fi
