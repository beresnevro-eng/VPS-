#!/bin/bash
# Статус reverse WireGuard hop (Fornex ↔ RU entry)
set -uo pipefail

echo "=== hop-status $(date -Is) ==="

if ! command -v wg >/dev/null 2>&1; then
  echo "FAIL wireguard-tools не установлены"
  exit 1
fi

if ! ip link show wg-hop &>/dev/null; then
  echo "FAIL интерфейс wg-hop отсутствует"
  systemctl is-active wg-quick@wg-hop 2>/dev/null || true
  exit 1
fi

echo "OK  wg-hop up"
wg show wg-hop

LOCAL_IP=$(ip -4 -o addr show wg-hop | awk '{print $4}' | cut -d/ -f1)
case "$LOCAL_IP" in
  10.99.0.1) PEER=10.99.0.2; ROLE=entry ;;
  10.99.0.2) PEER=10.99.0.1; ROLE=fornex ;;
  *) PEER=""; ROLE=unknown ;;
esac
echo "role=$ROLE local=$LOCAL_IP"

if [[ -n "$PEER" ]]; then
  if ping -c1 -W2 "$PEER" >/dev/null 2>&1; then
    echo "OK  ping $PEER"
  else
    echo "FAIL ping $PEER"
  fi
fi

if [[ "$ROLE" == "fornex" ]]; then
  if ss -tlnp 2>/dev/null | grep -q '10.99.0.2:11080'; then
    echo "OK  hop-socks 10.99.0.2:11080"
  else
    echo "FAIL hop-socks не слушает 10.99.0.2:11080"
  fi
elif [[ "$ROLE" == "entry" ]]; then
  if curl -sS --max-time 8 --socks5-hostname 10.99.0.2:11080 https://ifconfig.me 2>/dev/null; then
    echo
    echo "OK  SOCKS exit через Fornex"
  else
    echo "FAIL SOCKS через 10.99.0.2:11080"
  fi
fi

systemctl is-active --quiet xray 2>/dev/null && echo "OK  xray" || echo "FAIL xray"
if [[ "$ROLE" == "entry" ]]; then
  systemctl is-active --quiet hysteria-server 2>/dev/null && echo "OK  hysteria-server" || echo "FAIL hysteria-server"
  # avoid pipefail+grep -q SIGPIPE false negative
  if ss -ulnp 2>/dev/null | grep hysteria >/dev/null; then
    echo "OK  UDP :443 (HY2)"
  else
    echo "FAIL UDP :443 (HY2)"
  fi
fi
systemctl is-active --quiet wg-quick@wg-hop 2>/dev/null && echo "OK  wg-quick@wg-hop" || echo "FAIL wg-quick@wg-hop"
