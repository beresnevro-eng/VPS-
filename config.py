"""
Настройки бота. Секреты лучше задавать через переменные окружения
или файл .env (не коммитить реальные токены в git).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Корень проекта и путь к SQLite (лёгкая БД, без Redis)
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)
DB_PATH = Path(os.getenv("COUPLE_QUIZ_DB", str(BASE_DIR / "data" / "couple_quiz.db")))

def _env_int(name: str, default: int = 0) -> int:
    raw = os.getenv(name, str(default))
    if raw is None or str(raw).strip() == "":
        return default
    return int(raw)


# Продукт и персона (см. MANIFESTO.md)
PROJECT_NAME = os.getenv("PROJECT_NAME", "Шёпот")
ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "Люм")
# Совместимость со старым кодом
PRODUCT_NAME = os.getenv("PRODUCT_NAME", ASSISTANT_NAME)

# Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN", "ЗАМЕНИТЕ_НА_ТОКЕН_ОТ_BOTFATHER")

# Telegram ID партнёров (числа). Узнать: написать @userinfobot
PARTNER_A_ID = _env_int("PARTNER_A_ID", 0)
PARTNER_B_ID = _env_int("PARTNER_B_ID", 0)

# Имена для тёплых обращений в сообщениях (опционально)
PARTNER_A_NAME = os.getenv("PARTNER_A_NAME", "Партнёр A")
PARTNER_B_NAME = os.getenv("PARTNER_B_NAME", "Партнёр B")

# AI-провайдер: deepseek (основной) | groq (запасной)
AI_PROVIDER = os.getenv("AI_PROVIDER", "deepseek").strip().lower()

# DeepSeek (OpenAI-compatible). Ключ — в .env; пустая строка = провайдер недоступен.
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# Groq API (бесплатный запасной LLM)
# Ключ: https://console.groq.com/keys
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "ЗАМЕНИТЕ_НА_GROQ_API_KEY")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
# Актуальные модели Groq (llama-3.3 / 3.1 на free/dev tier отключены с 16.08.2026)
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_MODEL_FALLBACKS = tuple(
    m.strip()
    for m in os.getenv(
        "GROQ_MODEL_FALLBACKS",
        "openai/gpt-oss-120b,qwen/qwen3.6-27b,llama-3.3-70b-versatile",
    ).split(",")
    if m.strip()
)


def deepseek_configured() -> bool:
    return bool(DEEPSEEK_API_KEY) and not DEEPSEEK_API_KEY.startswith("ЗАМЕНИТЕ")


def groq_configured() -> bool:
    return bool(GROQ_API_KEY) and not GROQ_API_KEY.startswith("ЗАМЕНИТЕ")


def effective_ai_provider() -> str:
    """
    Выбирает провайдера без падения при отсутствии ключа.
    deepseek → если ключ есть; иначе fallback на groq; иначе deepseek (ошибка на вызове).
    """
    wanted = AI_PROVIDER if AI_PROVIDER in ("deepseek", "groq") else "deepseek"
    if wanted == "deepseek":
        if deepseek_configured():
            return "deepseek"
        if groq_configured():
            return "groq"
        return "deepseek"
    if groq_configured():
        return "groq"
    if deepseek_configured():
        return "deepseek"
    return "groq"

# Расписание ежедневного квиза (локальное время сервера)
QUIZ_HOUR = _env_int("QUIZ_HOUR", 10)
QUIZ_MINUTE = _env_int("QUIZ_MINUTE", 0)
DIGEST_HOUR = _env_int("DIGEST_HOUR", 11)
DIGEST_MINUTE = _env_int("DIGEST_MINUTE", 0)
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")

# Mini App API (лёгкий FastAPI в том же процессе, без Gunicorn)
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = _env_int("API_PORT", 8080)
# GitHub Pages origins для Mini App (см. docs/)
# Можно переопределить через API_CORS_ORIGINS в .env (через запятую)
_DEFAULT_CORS = [
    "https://beresnevro-eng.github.io",
    "https://beresnevro-eng.github.io/VPS-",
    "https://beresnevro-eng.github.io/shepot",
    "https://beresnevro-eng.github.io/couple-quiz-bot",
    "https://beresnevro-eng.github.io/whisper",
]
_raw_cors = os.getenv("API_CORS_ORIGINS", "").strip()
if _raw_cors and _raw_cors != "*":
    API_CORS_ORIGINS = [o.strip() for o in _raw_cors.split(",") if o.strip()]
elif _raw_cors == "*":
    API_CORS_ORIGINS = ["*"]
else:
    API_CORS_ORIGINS = list(_DEFAULT_CORS)

# Темы по умолчанию (ротация / случайный выбор)
DEFAULT_TOPICS = [
    "романтика, страсть и близость",
    "быт и домашние обязанности",
    "глубокий разговор о чувствах",
    "лёгкость, юмор и игра",
    "эмоциональная поддержка и забота",
]

# Коды настроений для ручного квиза и блокировок (см. MANIFESTO)
# surprise не блокируется как тема — это «случайный выбор» среди остальных
MOOD_CATALOG: dict[str, dict[str, str]] = {
    "romance": {
        "label": "❤️ Романтика",
        "prompt": "романтика, страсть и близость",
    },
    "routine": {
        "label": "🏠 Быт",
        "prompt": "быт и домашние обязанности",
    },
    "deep": {
        "label": "🌱 Глубокий разговор",
        "prompt": "глубокий разговор о чувствах и смысле вашей связи",
    },
    "fun": {
        "label": "😂 Лёгкость",
        "prompt": "лёгкость, юмор и игра без тяжёлых тем",
    },
    "support": {
        "label": "🤝 Поддержка",
        "prompt": "эмоциональная поддержка и забота",
    },
    "surprise": {
        "label": "🎲 Сюрприз",
        "prompt": "",
    },
}


def mood_label(code: str) -> str:
    item = MOOD_CATALOG.get(code)
    return item["label"] if item else code


def mood_prompt(code: str) -> str:
    item = MOOD_CATALOG.get(code)
    return (item["prompt"] if item else "") or code


def concrete_mood_codes() -> list[str]:
    """Темы, которые можно блокировать / выбирать случайно (без «сюрприза»)."""
    return [c for c in MOOD_CATALOG if c != "surprise"]


def partner_ids() -> tuple[int, ...]:
    """Список Telegram ID. В тесте достаточно только PARTNER_A_ID."""
    ids = [i for i in (PARTNER_A_ID, PARTNER_B_ID) if i]
    seen: set[int] = set()
    out: list[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    if not out:
        raise RuntimeError("Задайте хотя бы PARTNER_A_ID (числовой Telegram ID)")
    return tuple(out)


def is_solo_mode() -> bool:
    """True, если второго партнёра ещё нет — тестируем в одиночку."""
    return PARTNER_B_ID == 0 or PARTNER_B_ID == PARTNER_A_ID


def allowed_user_ids() -> set[int]:
    """Только эти пользователи могут пользоваться ботом."""
    return set(partner_ids())


def partner_name(telegram_id: int) -> str:
    if telegram_id == PARTNER_A_ID:
        return PARTNER_A_NAME
    if telegram_id == PARTNER_B_ID and PARTNER_B_ID:
        return PARTNER_B_NAME
    return "Участник"


def validate_runtime_config() -> list[str]:
    """Проверки перед запуском. Возвращает список предупреждений/ошибок."""
    problems: list[str] = []
    if not BOT_TOKEN or BOT_TOKEN.startswith("ЗАМЕНИТЕ"):
        problems.append("BOT_TOKEN не задан")
    if not deepseek_configured() and not groq_configured():
        problems.append(
            "Нет AI-ключа: задайте DEEPSEEK_API_KEY или GROQ_API_KEY в .env"
        )
    if not PARTNER_A_ID:
        problems.append("PARTNER_A_ID не задан (нужен числовой Telegram ID, не username)")
    return problems
