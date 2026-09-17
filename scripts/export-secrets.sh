#!/bin/bash
# Экспорт секретов в зашифрованный архив и отправка в Telegram.
# Интерактивно спросит пароль GPG (дважды).
set -euo pipefail

ROOT="/root/couple-quiz-bot"
PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY="python3"
fi

eval "$(
  cd "$ROOT" && "$PY" - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "/root/couple-quiz-bot")
import config
ids = list(config.partner_ids())
token = (config.BOT_TOKEN or "").strip()
if not ids or not token or token.startswith("ЗАМЕНИТЕ"):
    raise SystemExit("Нужны BOT_TOKEN и PARTNER_A_ID в .env")
def sh(s):
    return "'" + str(s).replace("'", "'\"'\"'") + "'"
print(f"BOT_TOKEN={sh(token)}")
print(f"ADMIN_CHAT_ID={sh(ids[0])}")
print(f"DB_PATH={sh(Path(config.DB_PATH).resolve())}")
PY
)"

STAMP=$(date +%Y%m%d_%H%M%S)
WORK=$(mktemp -d /tmp/shepot-secrets-XXXXXX)
ARCHIVE="$WORK/secrets-export-${STAMP}.tar"
ENCRYPTED="${ARCHIVE}.gpg"
STAGE="$WORK/stage"
mkdir -p "$STAGE"

echo "[export-secrets] собираю файлы…"

# config.py без обязательности — обычно без секретов, но на всякий случай
if [ -f "$ROOT/config.py" ]; then
  cp -a "$ROOT/config.py" "$STAGE/config.py"
fi
if [ -f "$ROOT/.env" ]; then
  cp -a "$ROOT/.env" "$STAGE/.env"
else
  echo "[export-secrets] WARN: .env не найден"
fi

# cloudflared credentials (если есть)
CF_DIR="${HOME}/.cloudflared"
if [ -d "$CF_DIR" ]; then
  mkdir -p "$STAGE/cloudflared"
  # только json/yml — не тащим лишнее
  find "$CF_DIR" -maxdepth 1 \( -name '*.json' -o -name 'config.yml' -o -name '*.pem' \) \
    -exec cp -a {} "$STAGE/cloudflared/" \;
fi

# манифест содержимого (без секретов)
{
  echo "exported_at=${STAMP}"
  echo "host=$(hostname 2>/dev/null || echo unknown)"
  echo "db_path=${DB_PATH}"
  ls -la "$STAGE" || true
  ls -la "$STAGE/cloudflared" 2>/dev/null || true
} > "$STAGE/MANIFEST.txt"

tar -C "$STAGE" -cf "$ARCHIVE" .
echo "[export-secrets] архив: $ARCHIVE"
echo "[export-secrets] введите пароль GPG для шифрования (симметричный AES)…"

# -c симметричное; пароль спросит у TTY
gpg --symmetric --cipher-algo AES256 --output "$ENCRYPTED" "$ARCHIVE"
rm -f "$ARCHIVE"

SIZE=$(du -h "$ENCRYPTED" | cut -f1)
CAPTION="🔐 Шёпот — export secrets ${STAMP}
Размер: ${SIZE}
Расшифровка: gpg -d secrets-export-….tar.gpg > secrets.tar && tar xf secrets.tar"

RESP=$(mktemp /tmp/tg_secrets_XXXXXX.json)
HTTP=$(curl -sS -w "%{http_code}" -o "$RESP" \
  -F "chat_id=${ADMIN_CHAT_ID}" \
  -F "document=@${ENCRYPTED}" \
  -F "caption=${CAPTION}" \
  "https://api.telegram.org/bot${BOT_TOKEN}/sendDocument" || true)

if [ "$HTTP" != "200" ] || ! grep -q '"ok":true' "$RESP"; then
  echo "[export-secrets] ERROR: Telegram HTTP=${HTTP}"
  cat "$RESP" || true
  echo "[export-secrets] файл оставлен: $ENCRYPTED"
  exit 2
fi

rm -f "$RESP" "$ENCRYPTED"
rm -rf "$WORK"
echo "[export-secrets] OK: отправлено в Telegram chat=${ADMIN_CHAT_ID}, локальные копии удалены"
