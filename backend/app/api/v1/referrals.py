from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.db import SessionLocal

from app.core.config import settings

router = APIRouter(prefix="/referrals", tags=["referrals"])
engine = create_async_engine(settings.database_url.replace("+psycopg2", "+asyncpg"), pool_pre_ping=True)

def _bot_link(code: str) -> str:
    username = (settings.bot_username or "").lstrip("@").strip()
    return f"https://t.me/{username}?start=ref_{code}" if username else ""

class MeOut(BaseModel):
    ok: bool = True
    link: str
    level1: int
    level2: int
    level3: int
    income: int
    balance: int
    min_withdrawal: int = 1000

@router.get("/me", response_model=MeOut)
async def me(tg_id: int = Query(...)):
    async with engine.begin() as conn:
        u = (await conn.execute(text(
            "SELECT id, COALESCE(ref_code,'') FROM users WHERE tg_id=:tg"
        ), {"tg": tg_id})).first()
        if not u:
            raise HTTPException(404, "user not found")
        user_id, code = int(u[0]), str(u[1] or "")

        if not code:
            code = await _ensure_ref_code(conn, user_id)

        try:
            lvl1 = (await conn.execute(text(
                "SELECT COUNT(*) FROM referrals WHERE parent_user_id=:u AND level=1"
            ), {"u": user_id})).scalar_one()

            lvl2 = (await conn.execute(text(
                "SELECT COUNT(*) FROM referrals WHERE parent_user_id=:u AND level=2"
            ), {"u": user_id})).scalar_one()

            lvl3 = (await conn.execute(text(
                "SELECT COUNT(*) FROM referrals WHERE parent_user_id=:u AND level=3"
            ), {"u": user_id})).scalar_one()
        except Exception:
            # фоллбек на users.referred_by
            lvl1 = (await conn.execute(text(
                "SELECT COUNT(*) FROM users WHERE referred_by=:u"
            ), {"u": user_id})).scalar_one()
            lvl2 = (await conn.execute(text("""
                SELECT COUNT(*) FROM users u2
                WHERE u2.referred_by IN (SELECT id FROM users WHERE referred_by=:u)
            """), {"u": user_id})).scalar_one()
            lvl3 = (await conn.execute(text("""
                SELECT COUNT(*) FROM users u3
                WHERE u3.referred_by IN (
                    SELECT id FROM users WHERE referred_by IN (SELECT id FROM users WHERE referred_by=:u)
                )
            """), {"u": user_id})).scalar_one()

        income_rub = balance_rub = 0
        try:
            bal = (await conn.execute(text(
                "SELECT COALESCE(balance_cents,0) FROM referral_balances WHERE user_id=:u"
            ), {"u": user_id})).scalar_one_or_none()
            if bal is not None:
                balance_rub = int(bal) // 100

            inc = (await conn.execute(text("""
                SELECT COALESCE(SUM(amount_cents),0)
                FROM referral_transactions
                WHERE user_id=:u AND amount_cents>0
            """), {"u": user_id})).scalar_one_or_none()
            if inc is not None:
                income_rub = int(inc) // 100
        except Exception:
            income_rub = 0
            balance_rub = 0

        return MeOut(
            link=_bot_link(code),
            level1=int(lvl1), level2=int(lvl2), level3=int(lvl3),
            income=income_rub, balance=balance_rub, min_withdrawal=1000
        )

@router.post("/track")
async def track(code: str = Query(..., min_length=3), tg_id: int = Query(...)):
    code = (code or "").strip().upper()
    async with engine.begin() as conn:
        # гарантируем наличие пользователя (как /users/seen)
        uid = (await conn.execute(text("SELECT id, ref_code, ref FROM users WHERE tg_id=:tg"), {"tg": tg_id})).mappings().first()
        if not uid:
            await conn.execute(text("INSERT INTO users (tg_id) VALUES (:tg) ON CONFLICT (tg_id) DO NOTHING"), {"tg": tg_id})
            uid = (await conn.execute(text("SELECT id, ref_code, ref FROM users WHERE tg_id=:tg"), {"tg": tg_id})).mappings().first()
        if not uid:
            raise HTTPException(404, "user not found")

        my_id = int(uid["id"])
        my_ref_code = (uid["ref_code"] or "")
        my_ref_saved = (uid["ref"] or "")

        up_id = (await conn.execute(text("SELECT id FROM users WHERE ref_code=:c"), {"c": code})).scalar_one_or_none()
        if not up_id:
            raise HTTPException(404, "ref code not found")

        if my_ref_code and code == my_ref_code:
            return {"ok": True, "self": True}

        if my_ref_saved:
            return {"ok": True, "note": "ref already set"}

        await conn.execute(
            text("UPDATE users SET ref=:c WHERE id=:id AND (ref IS NULL OR ref='')"),
            {"c": code, "id": my_id},
        )

    return {"ok": True}

@router.post("/generate")
async def generate_code(tg_id: int = Query(...)):
    async with engine.begin() as conn:
        uid = (await conn.execute(text(
            "SELECT id FROM users WHERE tg_id=:tg"
        ), {"tg": tg_id})).scalar_one_or_none()
        if not uid:
            raise HTTPException(404, "user not found")
        code = await _ensure_ref_code(conn, int(uid))
        return {"ok": True, "code": code}

async def _ensure_ref_code(conn, user_id: int) -> str:
    row = (await conn.execute(text(
        "SELECT ref_code FROM users WHERE id=:id"
    ), {"id": user_id})).scalar_one_or_none()
    if row:
        return str(row)
    import secrets, string
    abc = string.ascii_uppercase + string.digits
    while True:
        code = "".join(secrets.choice(abc) for _ in range(8))
        exists = (await conn.execute(text(
            "SELECT 1 FROM users WHERE ref_code=:c"
        ), {"c": code})).first()
        if not exists:
            await conn.execute(text(
                "UPDATE users SET ref_code=:c WHERE id=:id"
            ), {"c": code, "id": user_id})
            return code
