# backend/app/api/v1/subscriptions.py
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from typing import List, Dict, Any
from datetime import datetime
from sqlalchemy import create_engine, text
import os

from datetime import datetime, timezone

import os, logging, requests

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://")
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)

@router.get("/me")
def my_subscription(
    user_id: str | None = Query(None, description="internal users.id (строка или число)"),
    tg_id: int | None = Query(None, description="Telegram user id"),
):

    if not user_id and tg_id is None:
        raise HTTPException(400, "provide user_id or tg_id")

    with engine.connect() as conn:
        if not user_id and tg_id is not None:
            u = conn.execute(text("""
                SELECT id::text AS id
                FROM public.users
                WHERE tg_id = :tg
                LIMIT 1
            """), {"tg": tg_id}).mappings().first()
            if not u:
                raise HTTPException(404, "user not found")
            user_id = u["id"]

        row = conn.execute(text("""
            SELECT
              user_id::text AS user_id,
              COALESCE(plan, tariff_code) AS plan,
              tariff_code,
              expires_at AS active_until,
              expires_at,
              status
            FROM public.subscriptions
            WHERE user_id::text = :uid
            ORDER BY (status='active') DESC, expires_at DESC NULLS LAST
            LIMIT 1
        """), {"uid": str(user_id)}).mappings().first()

    if not row:
        return {"user_id": str(user_id), "plan": "free", "status": "inactive", "active_until": None, "expires_at": None}
    return dict(row)

@router.get("/current")
def subscription_current(tg_id: int = Query(..., description="Telegram user id")):
    """
    Статус подписки ТОЛЬКО из нашей БД (без NemiLing).
    Возвращает: plan, status (active|inactive|expired), expires_at (ISO Z|None), days_left (int|None).
    Никогда не 500.
    """
    def _pack(plan_name: str, status: str, expires_at_dt: datetime | None):
        expires_iso, days_left = None, None
        now = datetime.now(timezone.utc)
        if expires_at_dt:
            expires_iso = expires_at_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            seconds = (expires_at_dt - now).total_seconds()
            days_left = max(int((seconds + 86399) // 86400), 0)
            if status == "active" and expires_at_dt <= now:
                status = "expired"
        return {
            "plan": plan_name or "free",
            "status": status,
            "expires_at": expires_iso,
            "days_left": days_left,
        }

    try:
        with engine.connect() as conn:
            u = conn.execute(text("""
                SELECT id
                FROM public.users
                WHERE tg_id = :tg
                LIMIT 1
            """), {"tg": tg_id}).first()

            if not u:
                return _pack("free", "inactive", None)

            uid = u[0]

            row = conn.execute(text("""
                SELECT
                  s.status,
                  s.expires_at,
                  COALESCE(t.title, t.code) AS plan_name
                FROM public.subscriptions s
                LEFT JOIN public.tariffs t ON t.id = s.tariff_id
                WHERE s.user_id = :uid
                ORDER BY (s.status = 'active') DESC, s.expires_at DESC NULLS LAST
                LIMIT 1
            """), {"uid": uid}).first()

            if not row:
                return _pack("free", "inactive", None)

            status = str(row.status or "inactive").lower()
            exp_dt = row.expires_at  # timestamptz или None
            plan_name = (row.plan_name or "free")

            return _pack(plan_name, status, exp_dt)

    except Exception:
        logger.exception("subscription_current DB check failed (tg_id=%s)", tg_id)
        return _pack("free", "inactive", None)

