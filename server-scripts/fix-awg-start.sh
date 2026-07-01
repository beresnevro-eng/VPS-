#!/usr/bin/env bash
# Починка awg0 после ошибки I1= в конфиге
set -euo pipefail
CONF=/etc/amnezia/amneziawg/awg0.conf
sed -i '/^I1[[:space:]]*=/d' "$CONF" /root/amnesiawg-clients/*.conf 2>/dev/null || true
systemctl restart awg-quick@awg0
sleep 1
systemctl is-active awg-quick@awg0 && awg show awg0
