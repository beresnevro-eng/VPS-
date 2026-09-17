#!/bin/bash
# Установка cron для бэкапа (запускать на хосте от root).
set -euo pipefail
chmod +x /root/couple-quiz-bot/scripts/backup.sh
touch /var/log/shepot-backup.log
chmod 644 /var/log/shepot-backup.log
CRON_LINE='0 3 * * * /root/couple-quiz-bot/scripts/backup.sh >> /var/log/shepot-backup.log 2>&1'
(crontab -l 2>/dev/null | grep -v 'couple-quiz-bot/scripts/backup.sh' || true; echo "$CRON_LINE") | crontab -
echo "Installed:"
crontab -l | grep backup || true
systemctl is-active cron 2>/dev/null || systemctl is-active crond 2>/dev/null || true
