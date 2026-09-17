# Шёпот (Couple Quiz Bot)

Гибрид: Telegram-бот **Люм** + Mini App для пары.

**Стек:** aiogram 3 · FastAPI · SQLite · DeepSeek/Groq · APScheduler

## Документация

| Файл | Содержание |
|------|------------|
| [MANIFESTO.md](./MANIFESTO.md) | тон, этика, продукт |
| [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) | архитектура, API, схема БД |
| [docs/CHANGELOG.md](./docs/CHANGELOG.md) | спринты v1–v2 |
| [docs/BACKUP.md](./docs/BACKUP.md) | бэкап SQLite → Telegram |

## Быстрый старт

```bash
cd /root/couple-quiz-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# заполните BOT_TOKEN, PARTNER_*_ID, DEEPSEEK_API_KEY / GROQ_API_KEY

bash start.sh   # или: python main.py
```

Секреты только в `.env`. Пример конфига: `config.example.py`.  
Не коммитьте `.env`, `*.db`, `.cloudflared/`.

## Mini App

Статика в `docs/` (GitHub Pages). API — через Cloudflare Tunnel (`MINI_APP_URL` / `app.wspr.online`).

## Бэкап и секреты

```bash
bash scripts/backup.sh              # дамп БД → Telegram → удалить с диска
bash scripts/install-backup-cron.sh # cron 03:00
bash scripts/export-secrets.sh      # .env + cloudflared → GPG → Telegram
```
