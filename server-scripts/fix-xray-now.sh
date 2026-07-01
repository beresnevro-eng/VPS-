#!/usr/bin/env bash
# Починка Xray: permission denied + /run/xray
set -euo pipefail

echo "=== fix-xray-now ==="

id xray &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin xray

mkdir -p /var/log/xray /run/xray /usr/local/etc/xray
chown -R xray:xray /var/log/xray /run/xray
chmod 755 /var/log/xray /run/xray
touch /var/log/xray/access.log /var/log/xray/error.log
chown xray:xray /var/log/xray/access.log /var/log/xray/error.log
chmod 644 /var/log/xray/access.log /var/log/xray/error.log

if [[ -f /root/daily-telegram-security/xray-config.json ]]; then
  cp /root/daily-telegram-security/xray-config.json /usr/local/etc/xray/config.json
fi
chown xray:xray /usr/local/etc/xray/config.json
chmod 640 /usr/local/etc/xray/config.json

/usr/local/bin/xray run -test -config /usr/local/etc/xray/config.json

systemctl daemon-reload
systemctl reset-failed xray 2>/dev/null || true
systemctl restart xray
sleep 2

echo "---"
systemctl is-active xray
ss -tlnp | grep ':443' || { echo "FAIL: 443 not listening"; exit 1; }
echo "OK: Xray на :443"
