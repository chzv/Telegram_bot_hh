from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

try:
    from app.deps import get_session as get_db
except Exception:
    from app.db import get_db

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update

router = APIRouter(prefix="/users", tags=["users_profile"])  


from sqlalchemy import text

@router.get("/profile")
def users_profile(tg_id: int, db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT
          u.tg_id,
          u.username,
          COALESCE(u.hh_account_name, '') AS hh_account_name,
          EXISTS(SELECT 1 FROM hh_tokens t WHERE t.user_id = u.id) AS hh_connected
        FROM users u
        WHERE u.tg_id = :tg_id
    """), {"tg_id": tg_id}).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="user not found")

    return {
        "tg_id": row["tg_id"],
        "username": row["username"],
        "hh_connected": bool(row["hh_connected"]),
        "hh_account_name": row["hh_account_name"],
    }


async def save_hh_account_info(session: AsyncSession, tg_id: int, me: dict):
    hh_id = me.get("id")
    hh_name = " ".join(filter(None, [me.get("first_name"), me.get("last_name")]))
    await session.execute(
        update(User)
        .where(User.tg_id == tg_id)
        .values(
            hh_account_id=hh_id,
            hh_account_name=hh_name,
        )
    )
    await session.commit()
