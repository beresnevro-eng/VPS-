#!/bin/bash
# Статус reverse WireGuard hop (Fornex ↔ RU entry)
set -uo pipefail

SOCKS_PORT=11081

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
  if ss -tlnp 2>/dev/null | grep -E "10\\.99\\.0\\.2:${SOCKS_PORT}\\b" >/dev/null; then
    echo "OK  hop-socks 10.99.0.2:${SOCKS_PORT}"
  else
    echo "FAIL hop-socks не слушает 10.99.0.2:${SOCKS_PORT}"
  fi
  systemctl is-active --quiet hop-socks 2>/dev/null && echo "OK  hop-socks.service" || echo "FAIL hop-socks.service"
  systemctl is-active --quiet hop-forward-nat 2>/dev/null && echo "OK  hop-forward-nat" || echo "WARN hop-forward-nat"
elif [[ "$ROLE" == "entry" ]]; then
  EXIT_IP=$(curl -sS --max-time 8 --socks5-hostname "10.99.0.2:${SOCKS_PORT}" https://ifconfig.me 2>/dev/null || true)
  if [[ "$EXIT_IP" == "103.75.126.216" ]]; then
    echo "OK  SOCKS exit $EXIT_IP"
  else
    echo "FAIL SOCKS через 10.99.0.2:${SOCKS_PORT} (got '${EXIT_IP:-timeout}')"
  fi
  # Throughput probe — Instagram/video needs >>1 Mbps; warn if hop is capped
  SPEED=$(curl -sS --max-time 12 -o /dev/null -w '%{speed_download}' \
    --socks5-hostname "10.99.0.2:${SOCKS_PORT}" \
    "https://speed.cloudflare.com/__down?bytes=1000000" 2>/dev/null || echo 0)
  SPEED_MBPS=$(awk -v s="$SPEED" 'BEGIN{printf "%.2f", s*8/1000000}')
  echo "info hop_download≈${SPEED_MBPS} Mbps"
  awk -v s="$SPEED" 'BEGIN{exit !(s < 200000)}' && echo "WARN hop <1.6 Mbps — Reels/Stories будут буфериться"

  if systemctl is-active --quiet awg-quick@awg0 2>/dev/null; then
    AWG_IP=$(curl -sS --max-time 8 --interface 10.66.77.1 https://ifconfig.me 2>/dev/null || true)
    if [[ "$AWG_IP" == "103.75.126.216" ]]; then
      echo "OK  AWG policy-route exit $AWG_IP"
    else
      echo "WARN AWG exit '${AWG_IP:-timeout}' (ожидали Fornex)"
    fi
  fi
fi

systemctl is-active --quiet xray 2>/dev/null && echo "OK  xray" || echo "FAIL xray"
if [[ "$ROLE" == "entry" ]]; then
  systemctl is-active --quiet hysteria-server 2>/dev/null && echo "OK  hysteria-server" || echo "FAIL hysteria-server"
  if ss -ulnp 2>/dev/null | grep -E ':(8444)\b' >/dev/null; then
    echo "OK  UDP :8444 (HY2)"
  else
    echo "FAIL UDP :8444 (HY2)"
  fi
  systemctl is-active --quiet awg-quick@awg0 2>/dev/null && echo "OK  awg-quick@awg0" || echo "WARN awg-quick@awg0"
fi
systemctl is-active --quiet wg-quick@wg-hop 2>/dev/null && echo "OK  wg-quick@wg-hop" || echo "FAIL wg-quick@wg-hop"
