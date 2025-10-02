# backend/app/api/v1/admin_logs.py
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
import os, socket

router = APIRouter(prefix="/admin/logs", tags=["admin:logs"])

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

# ---- helpers ---------------------------------------------------------------

def _table_cols(conn, schema: str, table: str) -> set[str]:
    rows = conn.execute(text("""
      SELECT column_name
      FROM information_schema.columns
      WHERE table_schema=:s AND table_name=:t
    """), {"s": schema, "t": table}).scalars().all()
    return {r for r in rows}

def _pick(cols: set[str], *candidates: str) -> str | None:
    for c in candidates:
        if c in cols:
            return c
    return None

# ---- list -----------------------------------------------------------------

@router.get("")
def admin_logs_list(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str = "",
    t: str = "",  # info|error|warning, если такого поля нет — игнорируем
):
    with _engine.begin() as conn:
        cols = _table_cols(conn, "public", "admin_logs")

        # динамически подбираем реальные имена колонок
        id_col       = _pick(cols, "id") or "id"
        created_col  = _pick(cols, "created_at", "created", "ts", "timestamp", "time")
        action_col   = _pick(cols, "action", "event", "what")
        text_col     = _pick(cols, "text", "message", "msg", "details", "payload")
        type_col     = _pick(cols, "type", "level", "severity", "kind")
        user_id_col  = _pick(cols, "user_id")

        # SELECT-список
        sel = [f"l.{id_col} AS id"]
        if created_col: sel.append(f"l.{created_col} AS created_at")
        else:           sel.append("NULL::timestamptz AS created_at")
        if action_col:  sel.append(f"l.{action_col} AS action")
        else:           sel.append("NULL::text AS action")
        if type_col:    sel.append(f"l.{type_col} AS type")
        else:           sel.append("NULL::text AS type")
        if text_col:    sel.append(f"l.{text_col} AS text")
        else:           sel.append("NULL::text AS text")

        join = ""
        if user_id_col:
            sel.append(f"l.{user_id_col} AS user_id")
            # join users для имени
            sel.append("COALESCE(u.hh_account_name, u.username, u.email, u.id::text) AS user_name")
            join = f"LEFT JOIN users u ON u.id = l.{user_id_col}"
        else:
            sel.append("NULL::bigint AS user_id")
            sel.append("NULL::text   AS user_name")

        # WHERE
        where = ["1=1"]
        params = {"limit": limit, "offset": offset}
        if q and (text_col or action_col):
            parts = []
            if text_col:  parts.append(f"lower(l.{text_col}) LIKE :q")
            if action_col: parts.append(f"lower(l.{action_col}) LIKE :q")
            where.append("(" + " OR ".join(parts) + ")")
            params["q"] = f"%{q.lower()}%"
        if t and type_col:
            where.append(f"lower(l.{type_col}) = :t")
            params["t"] = t.lower()

        # ORDER BY
        if created_col:
            order_by = f"l.{created_col} DESC, l.{id_col} DESC"
        else:
            order_by = f"l.{id_col} DESC"

        sql = f"""
        SELECT {", ".join(sel)}
        FROM admin_logs l
        {join}
        WHERE {' AND '.join(where)}
        ORDER BY {order_by}
        LIMIT :limit OFFSET :offset
        """
        rows = conn.execute(text(sql), params).mappings().all()
        return {"items": rows, "limit": limit, "offset": offset, "count": len(rows)}

# ---- clear ----------------------------------------------------------------

@router.post("/clear")
def admin_logs_clear():
    with _engine.begin() as conn:
        conn.execute(text("TRUNCATE admin_logs RESTART IDENTITY"))
    return {"ok": True}
