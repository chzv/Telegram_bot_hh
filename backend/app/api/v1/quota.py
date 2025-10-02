# backend/app/api/v1/quota.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
from ..deps import get_session
from app.services.limits import quota_for_user

router = APIRouter(prefix="/quota", tags=["quota"])

@router.get("")
def get_quota(
    tg_id: int | None = Query(None),
    user_id: int | None = Query(None),
    db: Session = Depends(get_session),
):
    if user_id is None:
        if tg_id is None:
            raise HTTPException(status_code=422, detail="either tg_id or user_id is required")
        user_id = db.execute(text("SELECT id FROM users WHERE tg_id=:t LIMIT 1"), {"t": tg_id}).scalar_one_or_none()
        if not user_id:
            raise HTTPException(status_code=404, detail="user not found")

    q = quota_for_user(db, user_id)
    return {
        "tg_id": tg_id,
        "user_id": user_id,
        "tariff": q["tariff"],
        "limit": int(q["limit"]),
        "hard_cap": int(q["hard_cap"]),
        "used": int(q["used"]),
        "remaining": int(q["remaining"]),
        "reset_time_msk": q["reset_time"], 
    }
