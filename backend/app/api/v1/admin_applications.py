# backend/app/api/v1/admin_applications.py
from fastapi import APIRouter, Query
from typing import Optional
import os, socket
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url

router = APIRouter(prefix="/admin", tags=["admin"])

def _build_sync_dsn() -> str:
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        raise RuntimeError("No DATABASE_URL")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)\
             .replace("postgresql://",          "postgresql+psycopg2://", 1)
    try:
        host = make_url(dsn).host or ""
        if host:
            socket.gethostbyname(host)  # ensure resolvable
    except Exception:
        pass
    return dsn

_engine = create_engine(_build_sync_dsn(), pool_pre_ping=True, future=True)

# --- Вычисление статуса по последнему событию из логов ---
STATUS_EXPR = """
CASE
  WHEN ll.event IN ('viewed','resume_viewed','seen','read') THEN 'viewed'
  WHEN ll.event IN ('declined','rejected','denied','failed') THEN 'declined'
  WHEN ll.event IN ('invited','invite','offer')            THEN 'invited'
  WHEN a.status = 'error'                                  THEN 'error'
  WHEN a.status = 'sent'                                   THEN 'sent'
  ELSE COALESCE(a.status, 'sent')
END
"""

@router.get("/applications")
def admin_list_applications(
    q: Optional[str] = Query(None, description="поиск по tg/user/hh/vacancy/resume"),
    status: Optional[str] = Query(None, description="sent|viewed|declined|invited|error"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: str = Query("-id", description="id|-id|date|-date"),
):
    if sort == "id":
        sort_sql = "a.id ASC"
    elif sort == "-id":
        sort_sql = "a.id DESC"
    elif sort == "date":
        sort_sql = "a.created_at ASC"
    else:
        sort_sql = "a.created_at DESC"

    where_parts = ["1=1"]
    params = {"limit": limit, "offset": offset}

    if q:
        where_parts.append("""
        (
            u.username ILIKE :q
         OR u.hh_account_name ILIKE :q
         OR CAST(u.tg_id AS TEXT) ILIKE :q
         OR CAST(a.vacancy_id AS TEXT) ILIKE :q
         OR r.title ILIKE :q
        )
        """)
        params["q"] = f"%{q}%"

    if status:
        where_parts.append(f"({STATUS_EXPR}) = :st")
        params["st"] = status

    where_sql = " AND ".join(where_parts)

    sql = f"""
    WITH last_log AS (
      SELECT l.application_id, l.event
      FROM applications_log l
      JOIN (
        SELECT application_id, MAX(created_at) AS mx
        FROM applications_log
        GROUP BY application_id
      ) m ON m.application_id = l.application_id AND m.mx = l.created_at
    )
    SELECT
      a.id                                        AS app_id,
      u.id                                        AS user_id,
      u.tg_id                                     AS tg_id,
      COALESCE(u.hh_account_name, u.username, 'tg:'||u.tg_id::text) AS user_name,
      a.created_at                                AS created_at,
      r.title                                     AS resume_title,
      ('#' || a.vacancy_id::text)                 AS vacancy_code,
      '—'                                         AS company_name,
      {STATUS_EXPR}                               AS eff_status
    FROM applications a
    JOIN users u ON u.id = a.user_id
    LEFT JOIN last_log ll ON ll.application_id = a.id
    LEFT JOIN LATERAL (
      SELECT title
      FROM resumes rr
      WHERE rr.user_id = a.user_id
      ORDER BY rr.updated_at DESC NULLS LAST
      LIMIT 1
    ) r ON TRUE
    WHERE {where_sql}
    ORDER BY {sort_sql}
    LIMIT :limit OFFSET :offset
    """

    total_sql = f"""
    WITH last_log AS (
      SELECT l.application_id, l.event
      FROM applications_log l
      JOIN (
        SELECT application_id, MAX(created_at) AS mx
        FROM applications_log
        GROUP BY application_id
      ) m ON m.application_id = l.application_id AND m.mx = l.created_at
    )
    SELECT COUNT(*)
    FROM applications a
    JOIN users u ON u.id = a.user_id
    LEFT JOIN last_log ll ON ll.application_id = a.id
    LEFT JOIN LATERAL (
      SELECT title
      FROM resumes rr
      WHERE rr.user_id = a.user_id
      ORDER BY rr.updated_at DESC NULLS LAST
      LIMIT 1
    ) r ON TRUE
    WHERE {where_sql}
    """

    with _engine.begin() as conn:
        rows  = conn.execute(text(sql), params).fetchall()
        total = conn.execute(text(total_sql), params).scalar() or 0

    items = []
    for row in rows:
        app_id, user_id, tg_id, user_name, created_at, resume_title, vacancy_code, company_name, eff_status = row
        items.append({
            "appId":   int(app_id),
            "userId":  int(user_id),
            "tgId":    int(tg_id) if tg_id is not None else None,
            "userName": user_name,
            "date":     created_at.isoformat() if created_at else None,
            "resume":   resume_title or "—",
            "vacancy":  vacancy_code or "—",
            "company":  company_name or "—",
            "status":   eff_status or "sent",
        })

    return {"ok": True, "items": items, "limit": limit, "offset": offset, "total": total}
