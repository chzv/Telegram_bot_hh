# backend/app/api/v1/admin_notifications.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Body
from typing import Optional
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
import os, socket
from datetime import datetime

router = APIRouter(prefix="/admin/notifications", tags=["admin:notifications"])

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

# поддерживаем сегменты
SEGMENTS = {"premium", "no_subscription", "active", "auto_responses", "ai_responses"}

# ---------- модели ввода ----------
class CreateNotification(BaseModel):
    scope: str = "user"
    user_id: Optional[int] = None
    text: str
    scheduled_at: Optional[datetime] = None  # ISO-строка; по умолчанию now()

# ---------- list ----------
@router.get("")
def admin_notifications_list(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str = "",
    status: str = "all",
):
    where = ["1=1"]
    params = {"limit": limit, "offset": offset}
    if q:
        where.append("lower(n.text) like :q")
        params["q"] = f"%{q.lower()}%"
    if status and status != "all":
        where.append("n.status = :status")
        params["status"] = status

    sql = f"""
    select
      n.id, n.user_id, n.scope, n.text, n.scheduled_at, n.sent_at, n.status, n.error,
      u.tg_id, coalesce(u.hh_account_name, u.username, u.email, u.id::text) as user_name
    from notifications n
    left join users u on u.id = n.user_id
    where {' and '.join(where)}
    order by n.created_at desc
    limit :limit offset :offset
    """
    with _engine.begin() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return {"items": rows, "limit": limit, "offset": offset, "count": len(rows)}

# ---------- create ----------
@router.post("")
def admin_notifications_create(payload: CreateNotification = Body(...)):
    scope = (payload.scope or "").strip()

    # Валидация scope
    is_ok = False
    if scope in ("user", "all"):
        is_ok = True
    elif scope.startswith("segment:"):
        key = scope.split(":", 1)[1]
        is_ok = key in SEGMENTS
    if not is_ok:
        raise HTTPException(400, "invalid scope")

    if scope == "user" and not payload.user_id:
        raise HTTPException(400, "user_id is required for scope='user'")
    if not payload.text or not payload.text.strip():
        raise HTTPException(400, "text is required")

    params = {
        "user_id": payload.user_id,
        "scope": scope,
        "text": payload.text.strip(),
    }
    if payload.scheduled_at:
        params["scheduled_at"] = payload.scheduled_at
        sched_expr = ":scheduled_at"
    else:
        sched_expr = "now()"

    sql = f"""
    insert into notifications (user_id, scope, text, scheduled_at, status)
    values (:user_id, :scope, :text, {sched_expr}, 'pending')
    returning id
    """
    with _engine.begin() as conn:
        new_id = conn.execute(text(sql), params).scalar()
    return {"id": new_id, "status": "ok"}

@router.post("/{nid}/send-now")
def admin_notifications_send_now(nid: int):
    with _engine.begin() as conn:
        n = conn.execute(
            text("SELECT * FROM notifications WHERE id=:id FOR UPDATE"),
            {"id": nid},
        ).mappings().first()
        if not n:
            raise HTTPException(404, "notification not found")
        if n["status"] not in ("pending", "failed"):
            raise HTTPException(400, "notification is not in pending/failed state")

        # посчитали получателей для информации
        recipients = resolve_recipients(conn, n["scope"], n.get("user_id"))

        row = conn.execute(text("""
            UPDATE notifications
               SET scheduled_at = now(),
                   status       = 'pending',
                   updated_at   = now()
             WHERE id = :id
               AND status IN ('pending','failed')
         RETURNING id
        """), {"id": nid}).first()

        if not row:
            raise HTTPException(400, "notification is not in pending/failed state")

    return {"ok": True, "recipients": len(recipients)}

# ---------- cancel (отменить, если ещё не ушло) ----------
@router.delete("/{nid}")
def admin_notifications_cancel(nid: int):
    with _engine.begin() as conn:
        row = conn.execute(text("""
            update notifications
            set status = 'canceled', updated_at = now()
            where id = :id and status in ('pending','failed')
            returning id
        """), {"id": nid}).first()
    if not row:
        raise HTTPException(400, "notification cannot be canceled")
    return {"ok": True}

# ---------- резольвер получателей ----------
def resolve_recipients(conn, scope: str, user_id: int | None):
    """
    Возвращает список tg_id по значению scope.
    scope: 'all' | 'user' | 'segment:<key>'
    """
    if scope == "all":
        rows = conn.execute(text("SELECT tg_id FROM users WHERE tg_id IS NOT NULL"))
        return [r.tg_id for r in rows]

    if scope == "user":
        if not user_id:
            return []
        row = conn.execute(text("SELECT tg_id FROM users WHERE id=:uid"), {"uid": user_id}).first()
        return [row.tg_id] if row and row.tg_id else []

    if scope and scope.startswith("segment:"):
        key = scope.split(":", 1)[1]
        if key not in SEGMENTS:
            return []

        if key == "premium":
            sql = """
              SELECT u.tg_id
              FROM users u
              JOIN subscriptions s ON s.user_id = u.id
              WHERE u.tg_id IS NOT NULL AND s.status = 'active'
            """
            rows = conn.execute(text(sql))
            return [r.tg_id for r in rows]

        if key == "no_subscription":
            sql = """
              SELECT u.tg_id
              FROM users u
              LEFT JOIN subscriptions s ON s.user_id = u.id AND s.status = 'active'
              WHERE u.tg_id IS NOT NULL AND s.user_id IS NULL
            """
            rows = conn.execute(text(sql))
            return [r.tg_id for r in rows]

        if key == "active":
            sql = """
              SELECT tg_id FROM users
              WHERE tg_id IS NOT NULL AND last_seen_at >= now() - INTERVAL '30 days'
            """
            rows = conn.execute(text(sql))
            return [r.tg_id for r in rows]

        if key == "auto_responses":
            sql = """
              SELECT u.tg_id
              FROM users u
              JOIN auto_responses ar ON ar.user_id = u.id
              WHERE u.tg_id IS NOT NULL AND ar.active = TRUE
            """
            rows = conn.execute(text(sql))
            return [r.tg_id for r in rows]

        if key == "ai_responses":
            sql = """
              SELECT u.tg_id
              FROM users u
              JOIN ai_responses_settings a ON a.user_id = u.id
              WHERE u.tg_id IS NOT NULL AND a.enabled = TRUE
            """
            rows = conn.execute(text(sql))
            return [r.tg_id for r in rows]

    return []
