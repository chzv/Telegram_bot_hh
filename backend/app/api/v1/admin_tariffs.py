from __future__ import annotations
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
import os, socket

router = APIRouter(prefix="/admin/tariffs", tags=["admin:tariffs"])

# --- общий sync DSN ---
def _build_sync_dsn() -> str:
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        try:
            from app.core.config import import_settings as _imp
            settings = _imp()
            dsn = (getattr(settings, "database_url", "") or "").strip()
        except Exception:
            pass
    if not dsn:
        raise RuntimeError("No DATABASE_URL found")

    dsn = dsn.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)\
             .replace("postgresql://", "postgresql+psycopg2://", 1)
    try:
        url = make_url(dsn)
        host = (url.host or "")
        if host and not os.path.exists("/.dockerenv"):
            socket.gethostbyname(host)
    except Exception:
        pass
    return dsn

_engine = create_engine(_build_sync_dsn(), pool_pre_ping=True, future=True)

# ======= схемы =======
class Plan(BaseModel):
    id: Optional[int] = None
    period_days: int
    price_rub: int
    active: bool = True
    sort_order: int = 100

class TariffsSummary(BaseModel):
    free_replies: int
    plans: List[Plan]

# ======= helpers =======
def _load_free_replies(conn) -> int:
    row = conn.execute(text("""
      SELECT (value->>'count')::int AS cnt
      FROM app_settings WHERE key='free_replies'
    """)).first()
    return row.cnt if row and row.cnt is not None else 0

def _save_free_replies(conn, count: int):
    conn.execute(text("""
      INSERT INTO app_settings(key, value, updated_at)
      VALUES ('free_replies', jsonb_build_object('count', :c), now())
      ON CONFLICT (key) DO UPDATE
      SET value = EXCLUDED.value, updated_at = now()
    """), {"c": count})

# ======= endpoints =======

@router.get("", response_model=TariffsSummary)
def admin_tariffs_get():
    with _engine.begin() as conn:
        free_cnt = _load_free_replies(conn)
        plans = conn.execute(text("""
          SELECT id, period_days, price_rub, COALESCE(active, TRUE) AS active,
                 COALESCE(sort_order, 100) AS sort_order
          FROM tariffs
          ORDER BY sort_order, period_days
        """)).mappings().all()
        return {"free_replies": free_cnt, "plans": list(plans)}

class PatchFree(BaseModel):
    free_replies: int

@router.patch("")
def admin_tariffs_patch(payload: PatchFree = Body(...)):
    if payload.free_replies < 0 or payload.free_replies > 10000:
        raise HTTPException(400, "free_replies out of range")
    with _engine.begin() as conn:
        _save_free_replies(conn, payload.free_replies)
    return {"ok": True}

@router.put("/plan/{pid}")
def admin_tariff_update_plan(pid: int, plan: Plan):
    with _engine.begin() as conn:
        row = conn.execute(text("""
          UPDATE tariffs
          SET period_days=:d, price_rub=:p, active=:a, sort_order=:s
          WHERE id=:id
          RETURNING id
        """), {"id": pid, "d": plan.period_days, "p": plan.price_rub,
               "a": plan.active, "s": plan.sort_order}).first()
        if not row:
            raise HTTPException(404, "plan not found")
    return {"ok": True}

@router.post("/plan")
def admin_tariff_create_plan(plan: Plan):
    with _engine.begin() as conn:
        new_id = conn.execute(text("""
          INSERT INTO tariffs(period_days, price_rub, active, sort_order)
          VALUES (:d, :p, :a, :s)
          RETURNING id
        """), {"d": plan.period_days, "p": plan.price_rub,
               "a": plan.active, "s": plan.sort_order}).scalar()
    return {"id": new_id, "status": "ok"}

@router.delete("/plan/{pid}")
def admin_tariff_delete_plan(pid: int):
    with _engine.begin() as conn:
        row = conn.execute(text("DELETE FROM tariffs WHERE id=:id RETURNING id"), {"id": pid}).first()
        if not row:
            raise HTTPException(404, "plan not found")
    return {"ok": True}
