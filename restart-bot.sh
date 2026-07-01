#!/bin/bash
# Перезапуск бота ТОЛЬКО через systemd (не из sandbox Cursor).
set -euo pipefail
if systemctl restart xray-telegram-bot; then
  sleep 2
  systemctl is-active xray-telegram-bot
  journalctl -u xray-telegram-bot -n 5 --no-pager
  exit 0
fi
echo "systemctl недоступен — запустите на сервере: systemctl restart xray-telegram-bot" >&2
exit 1
