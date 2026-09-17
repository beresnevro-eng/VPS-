# Бэкап базы «Шёпот»

## Что это

Раз в неделю скрипт `scripts/backup.sh`:

1. Делает атомарный дамп SQLite (`.backup` — безопасно при работающем боте).
2. Проверяет `PRAGMA integrity_check`.
3. Сжимает gzip.
4. Отправляет файл в Telegram админу (`PARTNER_A_ID` из `config.py`).
5. **Удаляет** `.db.gz` с сервера после успешной отправки (диск 10 ГБ не забивается).

Токен и chat_id **не хардкодятся** — читаются из `config.py` / `.env`.

Путь к БД тоже из config (`COUPLE_QUIZ_DB`, по умолчанию `data/couple_quiz.db`).

> Имя файла в Telegram: `shepot_YYYYMMDD_HHMMSS.db.gz` (префикс «shepot», даже если файл БД на диске — `couple_quiz.db`).

## Расписание

Cron (каждый день в 03:00 серверного времени):

```cron
0 3 * * * /root/couple-quiz-bot/scripts/backup.sh >> /var/log/shepot-backup.log 2>&1
```

Установка одной командой:

```bash
bash /root/couple-quiz-bot/scripts/install-backup-cron.sh
```

Лог: `/var/log/shepot-backup.log`

Проверить cron:

```bash
systemctl status cron
crontab -l
tail -n 50 /var/log/shepot-backup.log
```

Ручной запуск:

```bash
bash /root/couple-quiz-bot/scripts/backup.sh
```

Если нет CLI `sqlite3`, скрипт сам использует Python `sqlite3.Connection.backup()` (тоже атомарно и безопасно).

## Где лежат бэкапы

После успешного запуска файл приходит **в личку боту «Шёпот»** (чат = ваш Telegram ID из `PARTNER_A_ID`).

На сервере после отправки файл **удаляется**. В `/tmp/shepot-backups` могут остаться только failed-бэкапы (если Telegram не принял) — их чистит `find` старше 24 часов, либо можно забрать вручную.

## Как восстановить

1. В Telegram найдите последнее сообщение с документом `shepot_YYYYMMDD_HHMMSS.db.gz`.
2. Скачайте на машину / сервер.
3. Распакуйте:

```bash
gunzip -k shepot_YYYYMMDD_HHMMSS.db.gz
# или: gzip -dk shepot_....db.gz
```

4. Остановите бота/API (чтобы не писали в старую БД).
5. Замените рабочую базу (путь смотрите в `.env` / `config.DB_PATH`, обычно):

```bash
cp /path/to/shepot_YYYYMMDD_HHMMSS.db /root/couple-quiz-bot/data/couple_quiz.db
# при необходимости поправьте права
```

6. Проверьте целостность:

```bash
sqlite3 /root/couple-quiz-bot/data/couple_quiz.db "PRAGMA integrity_check;"
```

7. Запустите бота снова.

## Как проверить, что бэкап свежий

- В Telegram: дата/время в caption (`бэкап 20260917_030001`).
- На сервере:

```bash
tail -n 20 /var/log/shepot-backup.log
# ожидаем строку: OK: ... отправлено ... файл удалён
```

- Если видите `ERROR` и путь к `.gz` — файл **не удалён**, его можно отправить руками или разобрать ошибку Telegram.

## Требования

- `sqlite3`, `gzip`, `curl`
- валидные `BOT_TOKEN` и `PARTNER_A_ID` в `.env`
- бот должен иметь возможность писать вам в личку (хотя бы один `/start` боту)
