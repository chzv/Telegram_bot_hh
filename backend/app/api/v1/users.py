from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, conint
from sqlalchemy import text
from sqlalchemy.orm import Session
from fastapi.responses import StreamingResponse
import csv, io

try:
    from app.deps import get_session as get_db
except Exception:
    from app.db import get_db

router = APIRouter(prefix="/users", tags=["users"])

# ---------- models ----------

class SeenIn(BaseModel):
    tg_id: conint(gt=0)
    username: str | None = Field(default=None, max_length=64)
    first_name: str | None = None
    last_name: str | None = None
    is_premium: bool | None = None
    lang: str | None = None
    ref: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None

class RegisterIn(BaseModel):
    tg_id: conint(gt=0)
    username: str | None = Field(default=None, max_length=64)

# ---------- helpers ----------

def _to_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()

def _days_left(dt: Optional[datetime]) -> Optional[int]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return int((dt - now).total_seconds() // 86400)

# ---------- routes ----------

@router.post("/seen")
def users_seen(p: SeenIn, db: Session = Depends(get_db)):
    """
    Идемпотентный апсерт пользователя по tg_id.
    Обновляет username (если передан) и last_seen.
    Если прилетели UTM — записывает их ТОЛЬКО если пусто (COALESCE),
    чтобы сохранить первичный источник.
    """
    db.execute(
        text("""
            INSERT INTO users (tg_id, username, created_at, last_seen, last_seen_at)
            VALUES (:tg, :un, now(), now(), now())
            ON CONFLICT (tg_id) DO UPDATE
               SET username     = COALESCE(EXCLUDED.username, users.username),
                   last_seen    = now(),
                   last_seen_at = now()
        """),
        {"tg": int(p.tg_id), "un": p.username},
    )
    if any([p.utm_source, p.utm_medium, p.utm_campaign]):
        db.execute(
            text("""
                UPDATE users
                   SET utm_source   = COALESCE(utm_source,   :s),
                       utm_medium   = COALESCE(utm_medium,   :m),
                       utm_campaign = COALESCE(utm_campaign, :c)
                 WHERE tg_id = :tg
            """),
            {"tg": int(p.tg_id), "s": p.utm_source, "m": p.utm_medium, "c": p.utm_campaign},
        )
    db.commit()
    return {"ok": True}

@router.post("/register")
def register_user(p: RegisterIn, db: Session = Depends(get_db)):
    """
    Обратная совместимость: то же самое, что /seen.
    """
    row = db.execute(
        text("""
            INSERT INTO users (tg_id, username, created_at, last_seen, last_seen_at)
            VALUES (:tg, :un, now(), now(), now())
            ON CONFLICT (tg_id) DO UPDATE
               SET username     = COALESCE(EXCLUDED.username, users.username),
                   last_seen    = now(),
                   last_seen_at = now()
            RETURNING id
        """),
        {"tg": int(p.tg_id), "un": p.username},
    ).first()
    db.commit()
    return {"ok": True, "user_id": int(row[0]) if row else None}

@router.get("")
def users_list(q: str | None = None,
               limit: int = 50,
               offset: int = 0,
               db: Session = Depends(get_db)):
    """
    Простой список для админки. Возвращает {total, items:[...]}.
    Поля строго из существующей схемы.
    """
    where = ""
    params = {"limit": limit, "offset": offset}
    if q:
        where = """
          WHERE (COALESCE(username,'') ILIKE :qq
             OR  COALESCE(hh_account_name,'') ILIKE :qq
             OR  CAST(tg_id AS text) ILIKE :qq)
        """
        params["qq"] = f"%{q}%"

    total = db.execute(text(f"SELECT COUNT(*) FROM users {where}"), params).scalar() or 0
    rows = db.execute(
        text(f"""
            SELECT
              u.id, u.tg_id, u.username,
              u.hh_account_id, u.hh_account_name,
              to_char(u.created_at,'YYYY-MM-DD HH24:MI:SS') AS created_at,
              to_char(u.last_seen,'YYYY-MM-DD HH24:MI:SS')  AS last_seen_at,
              -- UTM метки
              u.utm_source, u.utm_medium, u.utm_campaign,

              -- агрегат: сколько подписок всего
              COALESCE(s.subs_count_total, 0)::bigint AS subs_count_total,
              -- агрегат: общая сумма оплат в рублях
              COALESCE(p.total_cents, 0)::bigint / 100.0 AS revenue_total_rub,
              -- есть ли активные авто-правила
              EXISTS (
                SELECT 1 FROM auto_responses ar
                WHERE ar.user_id = u.id AND ar.active = TRUE
              ) AS auto_responses_active,
            
              -- сколько всего авто-правил у пользователя
              COALESCE((
                SELECT COUNT(*) FROM auto_responses ar2
                WHERE ar2.user_id = u.id
              ), 0) AS auto_responses_total
            
            FROM users u
            
            -- агрегаторы по подпискам и платежам
            LEFT JOIN LATERAL (
              SELECT COUNT(*)::bigint AS subs_count_total
              FROM subscriptions s
              WHERE s.user_id = u.id
            ) s ON TRUE

            LEFT JOIN LATERAL (
              SELECT SUM(amount_cents)::bigint AS total_cents
              FROM payments p
              WHERE p.user_id = u.id            ) p ON TRUE
              
            LEFT JOIN LATERAL (
              SELECT
                BOOL_OR(ar.active)       AS auto_responses_active,
                COALESCE(SUM(r.sent), 0) AS auto_responses_total
              FROM auto_responses ar
              LEFT JOIN auto_runs r ON r.auto_id = ar.id
              WHERE ar.user_id = u.id
            ) ar ON TRUE
            
            {where}
            ORDER BY u.id DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    ).mappings().all()

    return {"total": total, "items": rows}

@router.get("/profile")
def users_profile(tg_id: int = Query(..., description="Telegram user id"),
                  db: Session = Depends(get_db)):
    """
    Профиль для фронта/бота: связка HH + срок токена.
    """
    u = db.execute(
        text("""
            SELECT id, tg_id, username,
                   hh_account_id, hh_account_name,
                   created_at, last_seen
            FROM users
            WHERE tg_id = :tg
            LIMIT 1
        """),
        {"tg": tg_id},
    ).mappings().first()

    if not u:
        raise HTTPException(status_code=404, detail="user not found")

    tok = db.execute(
        text("SELECT access_token, expires_at FROM hh_tokens WHERE user_id = :uid"),
        {"uid": u["id"]},
    ).mappings().first()

    linked = bool(tok and tok.get("access_token"))
    exp_iso = _to_utc_iso(tok["expires_at"]) if tok and tok.get("expires_at") else None

    return {
        "tg_id": u["tg_id"],
        "user_id": u["id"],
        "username": u["username"],
        "created_at": _to_utc_iso(u["created_at"]),
        "last_seen": _to_utc_iso(u["last_seen"]),
        "hh_connected": linked,
        "hh_account_id": u["hh_account_id"],
        "hh_account_name": u["hh_account_name"],
        "hh_expires_at": exp_iso,
    }

@router.get("/stats")
def user_stats(tg_id: int = Query(..., description="Telegram user id"),
               db: Session = Depends(get_db)):
    u = db.execute(text("SELECT id FROM users WHERE tg_id=:tg LIMIT 1"), {"tg": tg_id}).first()
    registered = bool(u)
    linked = False
    exp_iso = None
    days = None
    if registered:
        tok = db.execute(
            text("SELECT expires_at FROM hh_tokens WHERE user_id=:uid"),
            {"uid": int(u[0])},
        ).first()
        if tok and tok[0]:
            linked = True
            exp_iso = _to_utc_iso(tok[0])
            days = _days_left(tok[0])
    return {
        "registered": registered,
        "linked": linked,
        "token_expires_at": exp_iso,
        "expires_in_days": days,
    }
