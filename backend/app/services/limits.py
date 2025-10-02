# backend/app/services/limits.py
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal
from sqlalchemy import text
from sqlalchemy.orm import Session

TZ_MSK = timezone(timedelta(hours=3))

def today_bounds_msk(now: Optional[datetime] = None):
    now = now.astimezone(TZ_MSK) if now else datetime.now(TZ_MSK)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

def reset_time_msk(now: Optional[datetime] = None) -> str:
    now = now.astimezone(TZ_MSK) if now else datetime.now(TZ_MSK)
    return (now.replace(hour=0, minute=0, second=0, microsecond=0)
               + timedelta(days=1)).strftime("%H:%M %d.%m.%Y")

def get_user_tariff(db: Session, user_id: int) -> Literal["free", "paid"]:
    row = db.execute(text("""
        SELECT 1
          FROM subscriptions
         WHERE user_id = :u
           AND status IN ('active','paid')
           AND (expires_at IS NULL OR now() < expires_at)
         LIMIT 1
    """), {"u": user_id}).first()
    return "paid" if row else "free"

def count_effective_today(db: Session, user_id: int) -> int:
    start_utc, end_utc = today_bounds_msk()
    row = db.execute(text("""
        SELECT COUNT(*)::int
          FROM applications
         WHERE user_id = :u
           AND created_at >= :start_utc
           AND created_at <  :end_utc
           AND COALESCE(LOWER(status), '') NOT IN ('canceled','cancelled')
    """), {"u": user_id, "start_utc": start_utc, "end_utc": end_utc}).first()
    return int(row[0]) if row else 0

def quota_for_user(db: Session, user_id: int) -> dict:
    # Итог по «созданным сегодня», чтобы лимит уменьшался сразу:
    tariff = get_user_tariff(db, user_id)
    tariff_limit = 200 if tariff == "paid" else 10
    hard_cap = 200
    daily_cap = min(tariff_limit, hard_cap)
    used = count_effective_today(db, user_id)
    remaining = max(0, daily_cap - used)
    return {
        "tariff": tariff,
        "limit": daily_cap,
        "hard_cap": hard_cap,
        "used": used,
        "remaining": remaining,
        "reset_time": reset_time_msk(),
        "plan": tariff,
        "used_today": used,
        "reset_at_msk": reset_time_msk(),
        "tz": "Europe/Moscow",
    }
