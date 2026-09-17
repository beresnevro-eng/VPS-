# Архитектура «Шёпот»

Краткий обзор гибрида: Telegram-бот + Mini App.

## Стек

| Слой | Технология |
|------|------------|
| Бот | **aiogram v3** (FSM, long polling) |
| HTTP API / Mini App backend | **FastAPI** (uvicorn, один процесс рядом с ботом) |
| БД | **SQLite** (`data/couple_quiz.db`, SQLAlchemy async) |
| LLM | **DeepSeek** (основной) + **Groq** (fallback) |
| Планировщик | APScheduler (квиз по preferred_hour, дайджест, пуши) |
| Фронт Mini App | Vanilla JS/CSS/HTML в `docs/` |

Секреты только в `.env` (см. `.env.example`, `config.example.py`). `config.py` читает окружение через `python-dotenv`.

## Cloudflare Tunnel

1. На VPS крутится бот + FastAPI (часто порт **8787**).
2. **cloudflared** поднимает named tunnel → публичный HTTPS (`app.wspr.online` или `*.trycloudflare.com`).
3. Mini App открывается с того же origin (или с token в query), чтобы `initData` / auth не терялись на редиректах GitHub Pages.
4. Credentials tunnel: `~/.cloudflared/*.json` — **не в git** (см. `.gitignore`, бэкап через `scripts/export-secrets.sh`).

Скрипты: `setup-named-tunnel.sh`, `HOST_MINIAPP_FIX.sh`.

## Mini App на GitHub Pages

- Статика: репозиторий → каталог `docs/` (`index.html`, `app.js`, `style.css`).
- Pages отдаёт UI; API-вызовы идут на tunnel (`SHEPOT_API_BASE` / `app.wspr.online`).
- Auth: Telegram WebApp `initData` **или** подпись `t=` с кнопки бота (`mini_auth.py`).
- Кэш ассетов: query `?v=YYYYMMDD…` при деплое.

## Основные API endpoints

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/health` | healthcheck |
| POST | `/api/auth` | вход (initData / token) |
| GET | `/api/profile` | портрет + партнёр + настройки |
| GET/POST | `/api/onboarding/*` | анкета Mini App (state/answer/skip/reset) |
| GET | `/api/quiz/active` | активный квиз + status_text |
| POST | `/api/quiz/start` | внеочередной квиз по mood |
| GET | `/api/quiz/{id}` | детали квиза |
| GET/POST | `/api/quiz/{id}/discussion` | приватный чат с Люмом |
| GET | `/api/history` | история |
| GET | `/api/streak` | серия |
| GET | `/api/moods` | темы |
| POST | `/api/topics/block` | блок темы |
| CRUD | `/api/notes` | приватные заметки |
| CRUD | `/api/hidden-questions` | скрытые вопросы |
| GET/POST | `/api/ideas*` | идеи свиданий |
| GET/POST | `/api/digest*` | недельный дайджест |
| GET | `/api/insights/*` | тренды / карта тем |
| POST | `/api/notifications/settings` | preferred_hour и пуши |

Статика UI также может отдаваться с API: `/app/...`.

## Схема данных (SQLite)

- **User** — участник пары (`telegram_id`, имя).
- **UserProfile** — онбординг, `gender`, портрет `ai_summary` / public / private, блокировки тем, preferred_hour, флаги пушей.
- **Quiz** — сессия квиза (`status`: collecting / exchanging / analyzed / closed…), тема, анализ, метрики.
- **Question** — вопросы квиза (`q_type`, options_json, construct).
- **Answer** — ответ пользователя (текст / selected_option, skipped).
- **WeeklyDigest** — итоги недели + actions_json.
- **DigestReply** — ответы на follow-up после дайджеста.
- **DateIdea** — идеи свиданий (proposed / saved / done).
- **UserNote** — приватные заметки (только автору).
- **HiddenQuestion** — скрытые вопросы в пул квиза (pending / used).
- **DiscussionMessage** — приватный диалог user↔Люм по `quiz_id`.

Пара жёстко ограничена `PARTNER_A_ID` / `PARTNER_B_ID`.

## Бэкапы

См. [BACKUP.md](./BACKUP.md): weekly dump → Telegram → удаление с диска.
