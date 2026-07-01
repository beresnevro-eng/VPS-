#!/bin/bash
# Восстановить /usr/local/etc/xray/config.json из зеркала или последнего бэкапа
set -euo pipefail
MAIN=/usr/local/etc/xray/config.json
MIRROR=/root/daily-telegram-security/xray-config.json
DIR=/usr/local/etc/xray
LOG=/var/log/restore-xray.log

log() { echo "$(date -Is) $*" | tee -a "$LOG"; }

if [[ -f "$MAIN" ]]; then
  /usr/local/bin/xray -test -config "$MAIN" >/dev/null 2>&1 && exit 0
fi

src=""
if [[ -f "$MIRROR" ]]; then
  src="$MIRROR"
else
  src=$(ls -1t "$DIR"/config.json.bak.* 2>/dev/null | head -1 || true)
fi

[[ -n "$src" && -f "$src" ]] || { log "no config source"; exit 1; }

cp "$src" "$MAIN"
chown nobody:nogroup "$MAIN" 2>/dev/null || chown root:root "$MAIN"
chmod 644 "$MAIN"
/usr/local/bin/xray -test -config "$MAIN"
cp "$MAIN" "$MIRROR"
chmod 600 "$MIRROR"
systemctl restart xray 2>/dev/null || true
log "restored from $src"
