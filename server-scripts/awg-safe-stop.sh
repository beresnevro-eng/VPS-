#!/usr/bin/env bash
# Безопасная остановка AWG — Xray не трогаем
set -euo pipefail
systemctl stop awg-quick@awg0 2>/dev/null || true
systemctl disable awg-quick@awg0 2>/dev/null || true
iptables -D ufw-before-forward -i awg0 -j ACCEPT 2>/dev/null || true
iptables -D ufw-before-forward -o awg0 -j ACCEPT 2>/dev/null || true
echo "AWG остановлен. Проверка Xray:"
systemctl is-active xray 2>/dev/null || true
ss -tlnp 2>/dev/null | grep ':443 ' || echo "WARN: проверьте :443"
