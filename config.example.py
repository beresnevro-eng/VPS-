"""
Пример конфигурации. Скопируйте в .env (секреты) — config.py читает только окружение.

  cp .env.example .env
  # заполните BOT_TOKEN, PARTNER_*_ID, AI-ключи

Реальные токены НЕ храните в этом файле и НЕ коммитьте .env.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

DB_PATH = Path(os.getenv("COUPLE_QUIZ_DB", str(BASE_DIR / "data" / "couple_quiz.db")))

PROJECT_NAME = os.getenv("PROJECT_NAME", "Шёпот")
ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "Люм")
PRODUCT_NAME = os.getenv("PRODUCT_NAME", ASSISTANT_NAME)

BOT_TOKEN = os.getenv("BOT_TOKEN", "your_token_here")
BOT_USERNAME = (os.getenv("BOT_USERNAME", "YourBotUsername") or "").lstrip("@")

PARTNER_A_ID = int(os.getenv("PARTNER_A_ID", "0") or 0)
PARTNER_B_ID = int(os.getenv("PARTNER_B_ID", "0") or 0)
PARTNER_A_NAME = os.getenv("PARTNER_A_NAME", "Partner A")
PARTNER_B_NAME = os.getenv("PARTNER_B_NAME", "Partner B")

AI_PROVIDER = os.getenv("AI_PROVIDER", "deepseek")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "your_deepseek_api_key_here")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "your_groq_api_key_here")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8080") or 8080)
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://your-pages-or-tunnel.example/index.html")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
