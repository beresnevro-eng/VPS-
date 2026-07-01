#!/usr/bin/env bash
# Экстренное восстановление VPN после сбоя UFW / AWG
set -euo pipefail

echo "=== Emergency VPN restore ==="

# 1) Остановить AWG — не трогает Xray
systemctl stop awg-quick@awg0 2>/dev/null || true
systemctl disable awg-quick@awg0 2>/dev/null || true

# 2) Восстановить UFW before.rules (если битый)
if grep -q "ufw-before-forward" /etc/ufw/before.rules 2>/dev/null && head -5 /etc/ufw/before.rules | grep -q "AmneziaWG"; then
  echo "[!] Восстанавливаю /etc/ufw/before.rules"
  cp /root/daily-telegram-security/server-scripts/ufw-before.rules.clean /etc/ufw/before.rules 2>/dev/null \
    || cp /root/ufw-before.rules.clean /etc/ufw/before.rules
fi

ufw --force reload 2>/dev/null || true

# 3) Xray config
bash /root/restore-xray-config.sh 2>/dev/null || true
if [[ ! -f /usr/local/etc/xray/config.json && -f /root/daily-telegram-security/xray-config.json ]]; then
  cp /root/daily-telegram-security/xray-config.json /usr/local/etc/xray/config.json
  chmod 644 /usr/local/etc/xray/config.json
fi

# 4) Права логов
mkdir -p /var/log/xray 2>/dev/null || true
mkdir -p /run/xray 2>/dev/null || true
chown -R root:root /var/log/xray 2>/dev/null || chmod -R 777 /var/log/xray 2>/dev/null || true

# 5) Запуск сервисов
systemctl daemon-reload
systemctl enable xray 2>/dev/null || true
systemctl restart xray
systemctl restart hysteria-server 2>/dev/null || true
systemctl restart xray-telegram-bot 2>/dev/null || true

sleep 2
echo "--- status ---"
systemctl is-active xray hysteria-server xray-telegram-bot 2>/dev/null || true
ss -tlnp | grep -E ':443|:8444|:2053' || echo "WARN: порты не слушают"
/usr/local/bin/xray -test -config /usr/local/etc/xray/config.json 2>&1 | tail -1
echo "=== Done. Проверьте VPN с телефона (/links iphone) ==="
