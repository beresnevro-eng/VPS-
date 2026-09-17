#!/bin/bash
set -euo pipefail
cd /root/couple-quiz-bot
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy SOCKS_PROXY SOCKS5_PROXY socks_proxy socks5_proxy
export NO_PROXY='*' no_proxy='*'
OUT=/tmp/regen_yulya_out.txt
{
  echo "START $(date -Is)"
  getent hosts api.deepseek.com || true
  .venv/bin/python regen_portrait.py 1219595852
  echo "EXIT=$?"
  .venv/bin/python - <<'PY'
import asyncio
from database import init_db, get_session, UserProfile
from sqlalchemy import select

async def main():
    await init_db()
    async with get_session() as session:
        p = await session.get(UserProfile, 1219595852)
        if not p:
            print("NO_PROFILE")
            return
        s = p.ai_summary or ""
        pub = p.ai_summary_public or ""
        priv = p.ai_summary_private or ""
        print(f"LEN_FULL={len(s)} LEN_PUBLIC={len(pub)} LEN_PRIVATE={len(priv)}")
        print("PUBLIC_200=" + pub[:200])
        print("PUBLIC_300=" + pub[:300])
        print("PRIVATE_300=" + priv[:300])
asyncio.run(main())
PY
  echo "DONE $(date -Is)"
} > "$OUT" 2>&1
