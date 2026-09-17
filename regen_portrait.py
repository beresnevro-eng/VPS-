#!/usr/bin/env python3
"""Пересобрать портрет Люма для пользователя с уже сохранённой анкетой."""

from __future__ import annotations

import asyncio
import sys

from database import (
    complete_profile,
    get_or_create_profile,
    get_session,
    init_db,
    mark_onboarding_done_pending_summary,
    profile_answers_split,
)
import ai_service
import config


async def main(tg_id: int) -> int:
    await init_db()
    async with get_session() as session:
        profile = await get_or_create_profile(session, tg_id)
        base, fu = profile_answers_split(profile)
        print(f"user={tg_id} base={len(base)} followup={len(fu)}")
        print(f"models: {config.GROQ_MODEL} | {config.GROQ_MODEL_FALLBACKS}")

    summary, public, private, ok = await ai_service.generate_profile_summary(base, fu)
    print("OK" if ok else "FAIL")
    print("FULL:", summary[:800])
    print("PUBLIC:", public[:400])
    print("PRIVATE:", private[:400])

    async with get_session() as session:
        if ok:
            await complete_profile(
                session,
                tg_id,
                summary,
                ai_summary_public=public,
                ai_summary_private=private,
            )
        else:
            await mark_onboarding_done_pending_summary(session, tg_id)
    return 0 if ok else 1


if __name__ == "__main__":
    uid = int(sys.argv[1]) if len(sys.argv) > 1 else config.PARTNER_A_ID
    raise SystemExit(asyncio.run(main(uid)))
