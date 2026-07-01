#!/usr/bin/env bash
# Маршрутизация AmneziaWG: UFW forward + NAT (handshake есть, трафика нет)
set -euo pipefail

AWG_SUBNET="${AWG_SUBNET:-10.66.66.0/24}"
AWG_PORT="${AWG_PORT:-51830}"
CONF=/etc/amnezia/amneziawg/awg0.conf
BEFORE=/etc/ufw/before.rules

log() { echo "[fix-awg-routing] $*"; }

detect_nic() {
  local nic
  nic=$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}')
  if [[ -z "$nic" || "$nic" == "lo" ]]; then
    nic=$(ip route get 8.8.8.8 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}')
  fi
  [[ -n "$nic" && "$nic" != "lo" ]] || nic=eth0
  echo "$nic"
}

NIC=$(detect_nic)
log "Интерфейс в интернет: $NIC"

  if [[ -f "$CONF" ]] && command -v sed >/dev/null; then
    sed -i "s/-o [^ ]\\+ -j MASQUERADE/-o ${NIC} -j MASQUERADE/g" "$CONF" 2>/dev/null || true
  fi

cat > /etc/sysctl.d/99-amnesiawg-forwarding.conf <<'EOF'
net.ipv4.ip_forward=1
net.ipv4.conf.all.forwarding=1
EOF
sysctl -p /etc/sysctl.d/99-amnesiawg-forwarding.conf >/dev/null

if command -v ufw >/dev/null 2>&1; then
  ufw allow "${AWG_PORT}/udp" comment "AmneziaWG" 2>/dev/null || true
  ufw route allow in on awg0 out on "$NIC" 2>/dev/null || true
  ufw route allow in on "$NIC" out on awg0 2>/dev/null || true

  if [[ -f "$BEFORE" ]]; then
    if ! grep -q "AmneziaWG NAT" "$BEFORE"; then
      log "Добавляю NAT в начало $BEFORE"
      tmp=$(mktemp)
      {
        echo "# AmneziaWG NAT (fix-awg-routing)"
        echo "*nat"
        echo ":POSTROUTING ACCEPT [0:0]"
        echo "-A POSTROUTING -s ${AWG_SUBNET} -o ${NIC} -j MASQUERADE"
        echo "COMMIT"
        echo ""
        cat "$BEFORE"
      } > "$tmp"
      mv "$tmp" "$BEFORE"
    fi
    if ! grep -q "ufw-before-forward -i awg0" "$BEFORE"; then
      log "Разрешаю forward awg0 в UFW"
      sed -i '/^COMMIT$/i\
# AmneziaWG forward\
-A ufw-before-forward -i awg0 -j ACCEPT\
-A ufw-before-forward -o awg0 -j ACCEPT\
' "$BEFORE"
    fi
    ufw reload 2>/dev/null || true
  fi
fi

systemctl restart awg-quick@awg0
sleep 2

log "=== Статус ==="
systemctl is-active awg-quick@awg0 || true
awg show awg0 2>/dev/null || true
echo ""
log "iPhone: выключите VPN → включите снова. Received должен расти."
