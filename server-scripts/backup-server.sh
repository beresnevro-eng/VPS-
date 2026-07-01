#!/bin/bash
# Ежедневный бэкап VPN-стека Fornex VPS
set -euo pipefail

BACKUP_DIR=/root/backups
RETENTION_DAYS=14
STAMP=$(date +%Y%m%d-%H%M%S)
ARCHIVE="${BACKUP_DIR}/fornex-vps-${STAMP}.tar.gz"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$BACKUP_DIR"

echo "=== Backup ${STAMP} ==="

mkdir -p "$TMP/usr/local/etc/xray"
cp -a /usr/local/etc/xray/config.json "$TMP/usr/local/etc/xray/" 2>/dev/null || true
cp -a /usr/local/etc/xray/config.json.bak.* "$TMP/usr/local/etc/xray/" 2>/dev/null || true

mkdir -p "$TMP/etc/hysteria"
cp -a /etc/hysteria/* "$TMP/etc/hysteria/" 2>/dev/null || true

mkdir -p "$TMP/daily-telegram-security"
rsync -a --exclude='__pycache__' /root/daily-telegram-security/ "$TMP/daily-telegram-security/"

mkdir -p "$TMP/root-links"
cp -a /root/hysteria2-link.txt /root/vpn-links.txt "$TMP/root-links/" 2>/dev/null || true

mkdir -p "$TMP/systemd"
for u in xray.service hysteria-server.service xray-telegram-bot.service; do
  cp "/etc/systemd/system/$u" "$TMP/systemd/" 2>/dev/null || true
done

mkdir -p "$TMP/cron.d"
cp /etc/cron.d/vps-autopilot "$TMP/cron.d/" 2>/dev/null || true

mkdir -p "$TMP/etc/fail2ban/jail.d"
cp -a /etc/fail2ban/jail.d/fornex-sshd.local "$TMP/etc/fail2ban/jail.d/" 2>/dev/null || true

cat >"$TMP/MANIFEST.txt" <<EOF
backup_time=${STAMP}
hostname=$(hostname -f 2>/dev/null || hostname)
xray_version=$(/usr/local/bin/xray version 2>/dev/null | head -1 || echo unknown)
EOF

tar -czf "$ARCHIVE" -C "$TMP" .
chmod 600 "$ARCHIVE"
echo "OK: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"

find "$BACKUP_DIR" -name 'fornex-vps-*.tar.gz' -mtime +${RETENTION_DAYS} -delete 2>/dev/null || true
