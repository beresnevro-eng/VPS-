#!/bin/bash
# Перезапуск бота + пересборка портрета (только на VPS, вне Cursor sandbox)
set -euo pipefail
cd /root/couple-quiz-bot

unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
unset SOCKS_PROXY SOCKS5_PROXY socks_proxy socks5_proxy
export NO_PROXY='*'
export no_proxy='*'

bash /root/couple-quiz-bot/start.sh
sleep 2
/root/couple-quiz-bot/.venv/bin/python /root/couple-quiz-bot/regen_portrait.py 267012313
echo "=== tail bot.log ==="
tail -n 40 /root/couple-quiz-bot/bot.log
