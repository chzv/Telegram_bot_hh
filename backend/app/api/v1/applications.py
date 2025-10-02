# backend/app/api/v1/applications.py
from __future__ import annotations

from typing import List, Optional, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text, bindparam
from sqlalchemy.dialects import postgresql as pg
from datetime import timezone
from app.services.limits import today_bounds_msk 

from app.core.config import settings

import re

router = APIRouter()

# --------- Schemas ---------
class QueueIn(BaseModel):
    tg_id: int = Field(..., ge=1)
    vacancies: List[int] = Field(..., min_items=1)
    resume_id: str = Field(..., min_length=1)          
    cover_letter: Optional[str] = None
    kind: Literal["manual", "auto"] = "manual"   
    campaign_id: Optional[int] = None 
# --------- DB helpers ---------
def _get_conn():
    eng = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    return eng.begin()  

def _get_user_id_by_tg(conn, tg_id: int) -> Optional[int]:
    row = conn.execute(
        text("SELECT id FROM users WHERE tg_id = :tg"),
        {"tg": tg_id},
    ).fetchone()
    return None if not row else int(row[0])

# --------- API ---------
@router.post("/hh/applications/queue")
def queue_applications(payload: QueueIn):
    if not payload.vacancies:
        raise HTTPException(status_code=400, detail="vacancies is empty")

    with _get_conn() as conn:
        uid = _get_user_id_by_tg(conn, payload.tg_id)
        if not uid:
            raise HTTPException(status_code=404, detail="user not found")

        row = conn.execute(text("""
            SELECT EXISTS(
                SELECT 1 FROM subscriptions
                 WHERE user_id=:u
                   AND status IN ('active','paid')
                   AND (expires_at IS NULL OR now() < expires_at)
            ) AS paid
        """), {"u": uid}).first()
        daily_cap = 200 if (row and row[0]) else 10

        start_utc, end_utc = today_bounds_msk()
        used = conn.execute(text("""
            SELECT COUNT(*)::int
              FROM applications
             WHERE user_id = :u
               AND created_at >= :start_utc
               AND created_at <  :end_utc
               AND COALESCE(LOWER(status), '') NOT IN ('canceled','cancelled')
        """), {"u": uid, "start_utc": start_utc, "end_utc": end_utc}).scalar_one()

        remaining = max(0, min(daily_cap, 200) - used)
        if remaining <= 0:
            return {"queued": 0}

        vids = list(map(int, payload.vacancies))[:remaining]

        raw = (payload.cover_letter or "")
        clean_cl = "" if re.fullmatch(r"\s*(?:-|—)?\s*(?:без\s+сопроводительн(?:ого\s+письма)?\.?)?\s*", raw, flags=re.I) else raw.strip()

        stmt = text("""
            INSERT INTO applications (user_id, vacancy_id, resume_id, cover_letter, kind, status, campaign_id)
            SELECT :uid, v, :rid, :cl, :kind, 'queued', :cid
            FROM unnest(:vids) AS v
            ON CONFLICT (user_id, vacancy_id)
            DO UPDATE SET
                resume_id     = EXCLUDED.resume_id,
                cover_letter  = EXCLUDED.cover_letter,
                kind          = EXCLUDED.kind,
                status        = 'queued',
                campaign_id   = COALESCE(EXCLUDED.campaign_id, applications.campaign_id),  -- ← удерживаем привязку, если есть
                error         = NULL,
                attempt_count = 0,
                next_try_at   = NULL,
                created_at    = CASE WHEN applications.created_at < :start_utc THEN now() ELSE applications.created_at END,
                updated_at    = now()
            RETURNING (created_at >= :start_utc AND created_at < :end_utc) AS is_today
        """).bindparams(
            bindparam("uid"),
            bindparam("rid"),
            bindparam("cl"),
            bindparam("kind"),
            bindparam("vids", type_=pg.ARRAY(pg.BIGINT)),
            bindparam("start_utc"),
            bindparam("end_utc"),
        )

        res = conn.execute(stmt, {
            "uid": uid,
            "rid": payload.resume_id,
            "cl": clean_cl,
            "kind": payload.kind,
            "vids": vids,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "cid": payload.campaign_id,
        })
        rows = res.fetchall()
        credited_today = sum(1 for r in rows if bool(r[0])) 
        conn.commit()
        return {"queued": int(credited_today)}
    
@router.get("/hh/applications/stats")
def apps_stats(tg_id: int = Query(..., ge=1)):
    """
    Статистика по уже обработанным заявкам (applications) + сколько сейчас в очереди (applications_queue).
    """
    with _get_conn() as conn:
        uid = _get_user_id_by_tg(conn, tg_id)
        if not uid:
            return {"queued": 0, "sent": 0, "error": 0}

        # очередь
        q_cnt = conn.execute(
            text("SELECT count(*) FROM applications_queue WHERE user_id = :u"),
            {"u": uid},
        ).scalar() or 0

        # статусы обработанных
        rows = conn.execute(
            text("""
                SELECT status, count(*)
                  FROM applications
                 WHERE user_id = :u
                 GROUP BY status
            """),
            {"u": uid},
        ).fetchall()
        by = {r[0]: int(r[1]) for r in rows}

        return {
            "queued": int(q_cnt),
            "sent": by.get("sent", 0),
            "error": by.get("error", 0),
        }
