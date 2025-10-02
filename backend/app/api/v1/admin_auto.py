# backend/app/api/v1/admin_auto.py
from __future__ import annotations
from fastapi import APIRouter, Query, HTTPException
from typing import Optional, Any
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
import os, socket, re
from datetime import datetime
from pydantic import BaseModel, Field

router = APIRouter(prefix="/admin", tags=["admin"])

class AdminAutoUpdate(BaseModel):
    active: Optional[bool] = None
    daily_limit: Optional[int] = Field(None, ge=1, le=1000)
    run_at: Optional[str] = None  # формат "HH:MM"
    name: Optional[str] = None

def _build_sync_dsn() -> str:
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        try:
            from app.core.config import settings
            dsn = (getattr(settings, "database_url", "") or "").strip()
        except Exception:
            dsn = ""
    if not dsn:
        raise RuntimeError("No DATABASE_URL found")

    # на случай asyncpg → psycopg2
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1).replace("postgresql://", "postgresql+psycopg2://", 1)

    try:
        url = make_url(dsn)
        host = (url.host or "")
        if host and not os.path.exists("/.dockerenv"):
            socket.gethostbyname(host)
    except Exception:
        pass
    return dsn

_engine = create_engine(_build_sync_dsn(), pool_pre_ping=True, future=True)

def _norm_status(s: Optional[str]) -> str:
    s = (s or "").strip().lower()
    if s in {"active", "1", "true", "yes", "активно", "активная"}:
        return "active"
    if s in {"inactive", "0", "false", "no", "неактивно", "неактивная"}:
        return "inactive"
    return "all"

def _like(q: str) -> str:
    return "%" + q.replace("%", "\\%").replace("_", "\\_") + "%"

@router.get("/auto-responses")
def admin_list_auto_responses(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str = Query("all"),
    q: Optional[str] = Query(None, description="поиск: username/ФИО/tg_id/title")
):
    where = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    st = _norm_status(status)
    if st != "all":
        where.append("ar.active = :is_active")
        params["is_active"] = (st == "active")

    if q:
        where.append("""
            (
                COALESCE(u.username,'') ILIKE :q OR
                (COALESCE(u.first_name,'') || ' ' || COALESCE(u.last_name,'')) ILIKE :q OR
                CAST(u.tg_id AS TEXT) ILIKE :q OR
                COALESCE(sr.title,'') ILIKE :q
            )
        """)
        params["q"] = _like(q)

    sql = text(f"""
        WITH agg AS (
          SELECT
            r.auto_id,
            MAX(r.created_at) AS last_run_at,
            SUM(r.sent)       AS sent_total,
            SUM(CASE WHEN r.created_at >= (NOW() - INTERVAL '24 hours') THEN r.sent ELSE 0 END) AS sent_24h
          FROM auto_runs r
          GROUP BY r.auto_id
        )
        SELECT
          ar.id,
          ar.user_id,
          u.tg_id,
          u.username, u.first_name, u.last_name, u.hh_account_name,
          ar.active, ar.run_at, ar.daily_limit, ar.name,
          sr.id AS saved_request_id,
          sr.title, sr.query, sr.query_params,
          sr.area, sr.employment, sr.schedule,
          sr.professional_roles, sr.search_fields,
          sr.cover_letter, sr.resume,
          a.last_run_at,
          COALESCE(a.sent_total, 0) AS sent_total,
          COALESCE(a.sent_24h,  0) AS sent_24h
        FROM auto_responses ar
        JOIN users u ON u.id = ar.user_id
        JOIN saved_requests sr ON sr.id = ar.saved_request_id
        LEFT JOIN agg a ON a.auto_id = ar.id
        WHERE {" AND ".join(where)}
        ORDER BY ar.updated_at DESC NULLS LAST, ar.id DESC
        LIMIT :limit OFFSET :offset
    """)

    with _engine.connect() as conn:
        rows = list(conn.execute(sql, params).mappings())

    def _uname(r):
       # приоритет: hh_account_name → "Имя Фамилия" → username → tg_id
        hh = (r.get("hh_account_name") or "").strip()
        fn = (r.get("first_name") or "").strip()
        ln = (r.get("last_name") or "").strip()
        un = (r.get("username") or "").strip()
        fio = (fn + (" " + ln if ln else "")).strip()
        if hh: return hh
        if fio: return fio
        if un:  return un
        try:
            return f"tg:{int(r.get('tg_id'))}"
        except Exception:
            return "—"

    out = []
    for r in rows:
        run_at_val = r["run_at"]
        run_at = run_at_val.strftime("%H:%M") if run_at_val else None
        last_run = r["last_run_at"].isoformat() if r["last_run_at"] else None
        out.append({
            "id": int(r["id"]),
            "user_id": int(r["user_id"]),
            "tg_id": int(r["tg_id"]),
            "user_name": _uname(r),
            "status": "active" if r["active"] else "inactive",
            "run_at": run_at,
            "daily_limit": int(r["daily_limit"] or 0),
            "name": r["name"] or "",
            "filters": {
                "title": r["title"] or "",
                "query": r["query"] or "",
                "query_params": r["query_params"] or "",
                "area": r["area"],
                "employment": r["employment"] or [],
                "schedule": r["schedule"] or [],
                "professional_roles": r["professional_roles"] or [],
                "search_fields": r["search_fields"] or [],
                "cover_letter": r["cover_letter"] or "",
                "resume": r["resume"] or None,
            },
            "last_run_at": last_run,
            "sent_total": int(r["sent_total"] or 0),
            "sent_24h": int(r["sent_24h"] or 0),
        })
    return {"items": out, "limit": limit, "offset": offset, "count": len(out)}

# --- Быстрый toggle ---
@router.post("/auto-responses/{auto_id}/toggle")
def admin_toggle_auto_response(auto_id: int):
    sql = text("UPDATE auto_responses SET active = NOT active, updated_at=NOW() WHERE id=:id RETURNING active")
    with _engine.connect() as conn:
        row = conn.execute(sql, {"id": auto_id}).first()
        if not row:
            raise HTTPException(404, "auto rule not found")
        conn.commit()
        return {"ok": True, "active": bool(row[0])}

# --- Частичное обновление полей ---
@router.patch("/auto-responses/{auto_id}")
def admin_update_auto_response(auto_id: int, payload: AdminAutoUpdate):
    sets = []
    params: dict[str, Any] = {"id": auto_id}

    if payload.active is not None:
        sets.append("active = :active")
        params["active"] = bool(payload.active)
    if payload.daily_limit is not None:
        sets.append("daily_limit = :daily_limit")
        params["daily_limit"] = int(payload.daily_limit)
    if payload.run_at is not None:
        if not re.fullmatch(r"\d{2}:\d{2}", payload.run_at.strip()):
            raise HTTPException(422, detail="run_at must be HH:MM")
        sets.append("run_at = (:run_at)::time")
        params["run_at"] = payload.run_at.strip()
    if payload.name is not None:
        sets.append("name = :name")
        params["name"] = payload.name.strip()

    if not sets:
        return {"ok": True, "updated": 0}

    sql = text(f"UPDATE auto_responses SET {', '.join(sets)}, updated_at=NOW() WHERE id=:id")
    with _engine.connect() as conn:
        res = conn.execute(sql, params)
        conn.commit()
        if res.rowcount == 0:
            raise HTTPException(404, "auto rule not found")
    return {"ok": True, "updated": int(res.rowcount)}
