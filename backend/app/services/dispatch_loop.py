# backend/app/services/dispatch_loop.py
import asyncio
from app.services.dispatcher import dispatch_once

DISPATCH_EVERY_SEC = 5

async def dispatch_forever():
    while True:
        try:
            await dispatch_once(dry_run=False, limit=50)
        except Exception as e:
            print("[dispatch_forever] error:", e)
        await asyncio.sleep(DISPATCH_EVERY_SEC)
