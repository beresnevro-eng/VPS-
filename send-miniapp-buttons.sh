#!/bin/bash
# Отправить свежие кнопки Mini App с токеном (запускать в SSH на VPS)
set -euo pipefail
cd /root/couple-quiz-bot
.venv/bin/python - <<'PY'
import asyncio, os
from pathlib import Path
from dotenv import load_dotenv
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import config
from onboarding import get_mini_app_url, mini_app_web_info_for, main_menu_keyboard

load_dotenv(Path('/root/couple-quiz-bot/.env'), override=True)

async def main():
    bot = Bot(token=os.environ['BOT_TOKEN'])
    print('MINI_APP_URL=', get_mini_app_url())
    for uid in config.allowed_user_ids():
        web = mini_app_web_info_for(uid)
        print('->', uid, web.url.split('?')[0], '+token')
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🌿 Открыть Шёпот', web_app=web)]
        ])
        await bot.send_message(
            uid,
            'Постоянный домен готов.\nОткройте кнопку ниже (не ссылку из Safari).',
            reply_markup=kb,
        )
        await bot.send_message(uid, 'Клавиатура обновлена ↓', reply_markup=main_menu_keyboard(uid))
    await bot.session.close()
    print('OK')

asyncio.run(main())
PY
