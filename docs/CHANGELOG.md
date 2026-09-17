# Changelog — Шёпот

## v2 — Mini App & продукт пары (2026)

### Спринт 6 — Онбординг в Mini App + UX
- Анкета (16 база + 3 AI) в Mini App, API `/api/onboarding/*`
- Род обращения (`gender`) и хелпер `gendered()`
- Активный квиз: продолжить / Mini App / сброс; улучшенный `/status`
- Фразы ожидания без кривого склонения имён

### Спринт 6b — Обсуждение с Люмом
- Приватный `DiscussionMessage` (бот + API + UI в деталях квиза)
- Deep link `discuss_{id}`, rate limits

### Спринт 6a — Умные напоминания
- `preferred_hour`, reactivation, пуш «разбор готов»
- Настройки уведомлений в Mini App

### Спринт 5b — Скрытые вопросы
- `HiddenQuestion`: CRUD в «Мы», встройка в квиз (~60%), privacy-лог

### Спринт 5a — Заметки
- Приватные `UserNote` (CRUD), контекст для дайджеста/идей

### Спринт 4 — Идеи свиданий
- Генерация / save / done, sheet UI

### Спринт 3 — Дайджест
- Воскресный weekly digest, actions, follow-up в боте

### Спринт 2 — Дашборд «Мы»
- Портреты public/private, динамика температуры, карта тем

### Спринт 1 — Home & streak
- Главная, CTA квиза, серия дней, история

## v1 — MVP бота

- Ежедневный / ручной квиз в Telegram (MC + open)
- Сбор ответов пары, сравнение, AI-анализ (Люм)
- Онбординг в чате (база + follow-ups → портрет)
- SQLite, aiogram 3, DeepSeek/Groq
- Манифест продукта (`MANIFESTO.md`)
