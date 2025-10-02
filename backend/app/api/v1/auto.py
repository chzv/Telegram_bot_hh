# hhbot/backend/app/api/v1/auto.py
from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Dict

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from app.core.config import settings
import httpx
from urllib.parse import urlparse, parse_qsl, urlencode
from urllib.parse import parse_qs
from app.services.auto_scheduler import dispatch_auto_once

router = APIRouter()

# --- нормализация querystring из hh.ru ---
ALLOW_KEYS = {
    "text", "area", "professional_role", "specialization",
    "experience", "employment", "schedule", "work_format",
    "only_with_salary", "salary", "currency",
    "search_field", "label", "order_by"
}
DROP_VALUES = {"", None}

def _normalize_query(qs_or_url: str) -> str:
    raw_qs = (qs_or_url or "").strip()
    if "?" in raw_qs:
        raw_qs = urlparse(raw_qs).query
    pairs = parse_qsl(raw_qs, keep_blank_values=True)
    filt = []
    for k, v in pairs:
        if k in {"page", "per_page"}:
            continue
        if k not in ALLOW_KEYS:
            continue
        if v in DROP_VALUES:
            continue
        filt.append((k, v))
    filt.sort(key=lambda kv: (kv[0], kv[1]))
    return urlencode(filt, doseq=True)


# ---------- Pydantic ----------
class AutoRuleUpsertIn(BaseModel):
    id: Optional[int] = None
    tg_id: int = Field(..., ge=1)

    name: Optional[str] = None
    title: Optional[str] = None

    query: Optional[str] = None
    query_params: Optional[str] = None

    area: Optional[int] = None
    employment: Optional[List[str]] = None
    schedule: Optional[List[str]] = None
    professional_roles: Optional[List[int]] = None
    search_fields: Optional[List[str]] = None
    cover_letter: Optional[str] = None

    resume_id: Optional[str] = None
    daily_limit: Optional[int] = 5
    active: Optional[bool] = True
    # если не прислали — дефолт "09:00"
    run_at: Optional[str] = None

    class Config:
        extra = "ignore"


class AutoRuleOut(BaseModel):
    id: int
    status: str = "ok"

class AutoActiveIn(BaseModel):
    tg_id: int = Field(..., ge=1)
    active: bool
    
# ---------- DB helpers ----------
def _conn():
    eng = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    return eng.connect()

def _user_id_by_tg(conn, tg_id: int) -> Optional[int]:
    row = conn.execute(text("SELECT id FROM users WHERE tg_id=:tg"), {"tg": tg_id}).fetchone()
    return int(row[0]) if row else None

def _nn(s: Optional[str]) -> str:
    return (s or "").strip()

# ---------- endpoint: upsert правила ----------
@router.post("/hh/auto/upsert", response_model=AutoRuleOut)
@router.post("/auto/upsert",    response_model=AutoRuleOut) 
def upsert_auto_rule(payload: AutoRuleUpsertIn) -> AutoRuleOut:
    with _conn() as conn:
        uid = _user_id_by_tg(conn, payload.tg_id)
        if not uid:
            raise HTTPException(status_code=404, detail="user not found")

        # 1) нормализуем поисковые параметры
        norm_qs = _normalize_query(payload.query_params or payload.query or "")

        # 2) гарантируем NOT NULL в saved_requests
        title = (_nn(payload.name) or _nn(payload.title) or "Моё авто-правило").strip()
        query = _nn(payload.query) or ""  

        params = {
            "uid": uid,
            "title": title,
            "query": query,
            "area": payload.area,
            "employment": payload.employment,
            "schedule": payload.schedule,
            "professional_roles": payload.professional_roles,
            "search_fields": payload.search_fields,
            "cover_letter": payload.cover_letter,
            "query_params": norm_qs or None,
        }

        # 3) upsert в saved_requests
        if payload.id:
            row = conn.execute(
                text("""
                    UPDATE saved_requests
                       SET title=:title,
                           query=:query,
                           area=:area,
                           employment=:employment,
                           schedule=:schedule,
                           professional_roles=:professional_roles,
                           search_fields=:search_fields,
                           cover_letter=:cover_letter,
                           query_params=:query_params,
                           updated_at=now()
                     WHERE id=:rid AND user_id=:uid
                 RETURNING id
                """),
                {"rid": payload.id, **params},
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="rule not found")
            rid = int(row[0])
        else:
            row = conn.execute(
                text("""
                    INSERT INTO saved_requests
                        (user_id, title, query, area, employment, schedule,
                         professional_roles, search_fields, cover_letter, query_params,
                         created_at, updated_at)
                    VALUES
                        (:uid, :title, :query, :area, :employment, :schedule,
                         :professional_roles, :search_fields, :cover_letter, :query_params,
                         now(), now())
                    RETURNING id
                """),
                params,
            ).fetchone()
            rid = int(row[0])

        # 4) upsert в auto_responses 
        rule_name = (_nn(payload.name) or _nn(payload.title) or "Моё авто-правило").strip()
        run_at_str = _nn(payload.run_at) or "09:00"  # столбец run_at у тебя NOT NULL

        ar_params = {
            "user_id": uid,
            "saved_request_id": rid,
            "resume_id": _nn(payload.resume_id) or None,
            "daily_limit": int(payload.daily_limit) if payload.daily_limit is not None else 0,
            "active": bool(payload.active if payload.active is not None else True),
            "run_at": run_at_str,  
            "name": rule_name,     
        }

        # 1) UPDATE, если запись уже есть
        updated = conn.execute(
            text("""
                UPDATE auto_responses
                SET user_id     = :user_id,
                    resume_id   = :resume_id,
                    name        = :name,                
                    daily_limit = :daily_limit,
                    active      = :active,
                    run_at      = CAST(:run_at AS time),
                    updated_at  = now()
                WHERE saved_request_id = :saved_request_id
            """),
            ar_params,
        ).rowcount

        # 2) INSERT, если не обновили
        if updated == 0:
            conn.execute(
                text("""
                    INSERT INTO auto_responses
                        (user_id, saved_request_id, resume_id, name, daily_limit, active, run_at, created_at, updated_at)
                    VALUES
                        (:user_id, :saved_request_id, :resume_id, :name, :daily_limit, :active,
                        CAST(:run_at AS time), now(), now())
                """),
                ar_params,
            )

        conn.commit()
        return AutoRuleOut(id=rid)


# ---------- endpoint: планирование очереди ----------
@router.post("/hh/auto/plan")
@router.post("/auto/plan")  
async def plan_auto() -> Dict[str, int]:
    """
    Вызывает сервис планировщика (HH API + массовая вставка в applications).
    Возвращает {"queued": N}
    """
    res = await dispatch_auto_once()
    return res

@router.get("/hh/auto/status")
@router.get("/auto/status")
def auto_status(tg_id: int = Query(..., ge=1)):
    with _conn() as conn:
        uid = _user_id_by_tg(conn, tg_id)
        if not uid:
            raise HTTPException(status_code=404, detail="user not found")

        # Есть ли ХОТЯ БЫ одно активное правило?
        active = bool(conn.execute(
            text("SELECT 1 FROM auto_responses WHERE user_id=:u AND active = TRUE LIMIT 1"),
            {"u": uid}
        ).fetchone())

        # Счётчики по авто-заявкам
        counts = conn.execute(text("""
            SELECT
              COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE) AS today_count,
              COUNT(*)                                              AS total_count
            FROM applications
            WHERE user_id = :u AND kind = 'auto'
        """), {"u": uid}).fetchone() or (0, 0)
        today_count = int(counts[0] or 0)
        total_count = int(counts[1] or 0)

        # Любое (самое свежее) правило для отображения ссылки и времени запуска
        row = conn.execute(text("""
            SELECT sr.query_params, ar.run_at
            FROM auto_responses ar
            JOIN saved_requests sr ON sr.id = ar.saved_request_id
            WHERE ar.user_id = :u
            ORDER BY ar.updated_at DESC NULLS LAST, ar.id DESC
            LIMIT 1
        """), {"u": uid}).fetchone()
        qparams = row[0] if row else None
        run_at  = row[1] if row else None

        # Дата самого первого запуска (по факту работы планировщика)
        start_d = conn.execute(text("""
            SELECT MIN(r.d)
            FROM auto_runs r
            JOIN auto_responses ar ON ar.id = r.auto_id
            WHERE ar.user_id = :u
        """), {"u": uid}).scalar()

        start_date = start_d.strftime("%d.%m.%Y") if start_d else "Не указано"
        start_time = run_at.strftime("%H:%M") if run_at else "Не указано"

        return {
            "active": active,
            "start_date": start_date,
            "start_time": start_time,
            "today_count": today_count,
            "total_count": total_count,
            "search_by_url": bool(qparams),
            "hh_url": f"https://hh.ru/search/vacancy?{qparams}" if qparams else None,
        }

@router.post("/hh/auto/active")
@router.post("/auto/active")
def set_auto_active(body: AutoActiveIn) -> dict:
    with _conn() as conn:
        uid = _user_id_by_tg(conn, body.tg_id)
        if not uid:
            raise HTTPException(status_code=404, detail="user not found")

        res = conn.execute(
            text("""
                UPDATE auto_responses
                   SET active=:a, updated_at=now()
                 WHERE user_id=:u
            """),
            {"a": bool(body.active), "u": uid},
        )
        conn.commit()
        return {"active": bool(body.active), "affected": int(res.rowcount)}
