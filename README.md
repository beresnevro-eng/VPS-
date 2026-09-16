# Couple Quiz Bot — MVP

Лёгкий Telegram-бот для пары: ежедневный квиз + AI-разбор (Groq).
Стек: Python 3.10+, aiogram 3, SQLite, APScheduler. Без Docker/Redis.

## Уже создано

| Файл | Назначение |
|------|------------|
| `config.py` | токены, ID партнёров, расписание, темы |
| `database.py` | SQLite + модели User/Quiz/Question/Answer |
| `ai_service.py` | Groq: генерация вопросов и анализ |
| `requirements.txt` | зависимости |

## Следующий шаг

Нужны `handlers.py`, `scheduler.py`, `main.py` — скажи, и допишу.

## Быстрый старт (после появления main.py)

```bash
cd /root/couple-quiz-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export BOT_TOKEN="..."
export GROQ_API_KEY="..."
export PARTNER_A_ID=123
export PARTNER_B_ID=456
export PARTNER_A_NAME="Я"
export PARTNER_B_NAME="Жена"
export TIMEZONE="Europe/Moscow"

python main.py
```
