#!/bin/bash
# Еженедельный бэкап SQLite «Шёпот» → Telegram → удаление с сервера.
set -euo pipefail

ROOT="/root/couple-quiz-bot"
PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY="python3"
fi

# ==== Настройки из config.py (без хардкода секретов) ====
eval "$(
  cd "$ROOT" && "$PY" - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "/root/couple-quiz-bot")
import config

db = Path(config.DB_PATH).resolve()
ids = list(config.partner_ids())
if not ids:
    raise SystemExit("PARTNER_A_ID не задан")
token = (config.BOT_TOKEN or "").strip()
if not token or token.startswith("ЗАМЕНИТЕ"):
    raise SystemExit("BOT_TOKEN не задан")

def sh(s: object) -> str:
    return "'" + str(s).replace("'", "'\"'\"'") + "'"

print(f"DB_PATH={sh(db)}")
print(f"BOT_TOKEN={sh(token)}")
print(f"ADMIN_CHAT_ID={sh(ids[0])}")
PY
)"

BACKUP_DIR="/tmp/shepot-backups"
KEEP_LOCAL_HOURS=24

# Лог: предпочитаем /var/log, иначе рядом с проектом
if [ -w /var/log ] || touch /var/log/shepot-backup.log 2>/dev/null; then
  : # cron уже пишет в /var/log/shepot-backup.log
fi

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/shepot_${DATE}.db"
LOG_TAG="[backup $(date -Is)]"

mkdir -p "$BACKUP_DIR"
echo "${LOG_TAG} start DB=${DB_PATH}"

if [ ! -f "$DB_PATH" ]; then
  echo "${LOG_TAG} ERROR: БД не найдена: $DB_PATH"
  exit 1
fi

# ==== Безопасный дамп + integrity (sqlite3 CLI или Python) ====
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"
  INTEGRITY=$(sqlite3 "$BACKUP_FILE" "PRAGMA integrity_check;")
else
  echo "${LOG_TAG} sqlite3 CLI нет — используем Python sqlite3"
  INTEGRITY=$(
    DB_PATH="$DB_PATH" BACKUP_FILE="$BACKUP_FILE" "$PY" - <<'PY'
import os, sqlite3
src = os.environ["DB_PATH"]
dst = os.environ["BACKUP_FILE"]
src_conn = sqlite3.connect(src)
try:
    dst_conn = sqlite3.connect(dst)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
finally:
    src_conn.close()
chk = sqlite3.connect(dst)
try:
    print(chk.execute("PRAGMA integrity_check;").fetchone()[0])
finally:
    chk.close()
PY
  )
fi

if [ "$INTEGRITY" != "ok" ]; then
  echo "${LOG_TAG} ERROR: integrity_check failed: $INTEGRITY"
  rm -f "$BACKUP_FILE"
  exit 1
fi

ORIGINAL_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
gzip -f "$BACKUP_FILE"
BACKUP_FILE_GZ="${BACKUP_FILE}.gz"
COMPRESSED_SIZE=$(du -h "$BACKUP_FILE_GZ" | cut -f1)

CAPTION="🌿 Шёпот — бэкап ${DATE}
Размер: ${ORIGINAL_SIZE} → ${COMPRESSED_SIZE}
Integrity: ok
Источник: ${DB_PATH}"

RESP_FILE=$(mktemp /tmp/tg_backup_XXXXXX.json)
HTTP_CODE=$(curl -sS -w "%{http_code}" -o "$RESP_FILE" \
  -F "chat_id=${ADMIN_CHAT_ID}" \
  -F "document=@${BACKUP_FILE_GZ}" \
  -F "caption=${CAPTION}" \
  "https://api.telegram.org/bot${BOT_TOKEN}/sendDocument" || true)

if [ "$HTTP_CODE" != "200" ]; then
  echo "${LOG_TAG} ERROR: Telegram HTTP ${HTTP_CODE}"
  cat "$RESP_FILE" || true
  echo "${LOG_TAG} файл оставлен: ${BACKUP_FILE_GZ}"
  exit 2
fi

if ! grep -q '"ok":true' "$RESP_FILE"; then
  echo "${LOG_TAG} ERROR: Telegram ok:false"
  cat "$RESP_FILE" || true
  echo "${LOG_TAG} файл оставлен: ${BACKUP_FILE_GZ}"
  exit 3
fi

rm -f "$RESP_FILE"
rm -f "$BACKUP_FILE_GZ"
echo "${LOG_TAG} OK: ${DATE}, ${ORIGINAL_SIZE}→${COMPRESSED_SIZE}, отправлено chat=${ADMIN_CHAT_ID}, файл удалён"

find "$BACKUP_DIR" -type f -mmin +$((KEEP_LOCAL_HOURS * 60)) -delete 2>/dev/null || true
