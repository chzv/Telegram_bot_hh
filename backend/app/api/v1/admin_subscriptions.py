from fastapi import APIRouter, Query
from sqlalchemy import create_engine, text
import os

router = APIRouter(prefix="/admin", tags=["admin"])

def _engine():
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql+psycopg2://" + dsn.split("://", 1)[1]
    elif dsn.startswith("postgresql://"):
        dsn = "postgresql+psycopg2://" + dsn.split("://", 1)[1]
    return create_engine(dsn, pool_pre_ping=True, future=True)

def _has_column(conn, table: str, col: str) -> bool:
    sql = """
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema='public' AND table_name=:t AND column_name=:c
    LIMIT 1
    """
    return conn.execute(text(sql), {"t": table, "c": col}).first() is not None

@router.get("/tariffs")
def admin_list_tariffs():
    """
    Справочник тарифов для админки: id, code, title (+ price_minor, если колонка есть).
    Без падающих пробных SELECT — только через information_schema.
    """
    eng = _engine()
    with eng.connect() as conn:
        has_price_minor = _has_column(conn, "tariffs", "price_minor")

        select_cols = "id, code, title" + (", price_minor" if has_price_minor else "")
        rows = conn.execute(text(f"SELECT {select_cols} FROM tariffs ORDER BY id")).mappings().all()

    items = []
    for r in rows:
        item = {"id": int(r["id"]), "code": r["code"], "title": r["title"]}
        if has_price_minor:
            item["price_minor"] = int(r["price_minor"]) if r["price_minor"] is not None else None
        items.append(item)
    return {"ok": True, "items": items}

@router.get("/subscriptions")
def admin_list_subscriptions(
    search: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    # ---- 1) общий COUNT без лимитов/офсета ----
    count_sql = """
    SELECT COUNT(*)
    FROM users u
    WHERE (:q = '' OR u.username ILIKE :pat OR u.email ILIKE :pat OR CAST(u.id AS TEXT) ILIKE :pat)
    """

    # ---- 2) страничная выборка ----
    sql = """
    WITH last_sub AS (
      SELECT DISTINCT ON (s.user_id)
        s.user_id,
        CASE
          WHEN s.status IS NULL OR s.status = '' THEN 'inactive'
          ELSE s.status
        END AS status,
        s.expires_at AS end_at,
        COALESCE(t.title, t.code, '—') AS tariff_title,
        NULL::numeric AS amount_rub
      FROM subscriptions s
      LEFT JOIN tariffs t ON t.id = s.tariff_id
      ORDER BY s.user_id, (s.status = 'active') DESC, s.expires_at DESC NULLS LAST
    )
    SELECT
      u.id AS user_id,
      COALESCE(u.username, u.email, '—') AS user_name,
      ls.tariff_title AS type,
      ls.end_at AS end_date,
      COALESCE(ls.status, 'inactive') AS status,
      COALESCE(p.total_cents, 0) / 100.0 AS amount
    FROM users u
    LEFT JOIN last_sub ls ON ls.user_id = u.id
    LEFT JOIN (
      SELECT user_id, SUM(amount_cents)::bigint AS total_cents
      FROM payments
      GROUP BY user_id
    ) p ON p.user_id = u.id
    WHERE (:q = '' OR u.username ILIKE :pat OR u.email ILIKE :pat OR CAST(u.id AS TEXT) ILIKE :pat)
    ORDER BY u.id
    LIMIT :limit OFFSET :offset
    """

    eng = _engine()
    with eng.connect() as conn:
        total = conn.execute(
            text(count_sql),
            {"q": search, "pat": f"%{search}%"}
        ).scalar() or 0

        # текущая страница
        rows = conn.execute(
            text(sql),
            {"q": search, "pat": f"%{search}%", "limit": limit, "offset": offset},
        ).mappings().all()

    items = [{
        "userId": r["user_id"],
        "userName": r["user_name"],
        "type": r["type"] or "—",
        "endDate": r["end_date"],
        "status": r["status"],
        "amount": float(r["amount"]) if r["amount"] is not None else 0.0,
    } for r in rows]

    return {"ok": True, "items": items, "limit": limit, "offset": offset, "total": int(total)}

from fastapi import HTTPException

@router.get("/user-lookup") 
def admin_resolve_user(q: str = Query(..., min_length=1, description="ID или username")):
    """
    Возвращает id пользователя по нику (username) или по числовому ID.
    Пример: /api/v1/admin/user-lookup?q=@ZoiaCh
    """
    q = (q or "").strip().lstrip("@")
    eng = _engine()
    with eng.connect() as conn:
        if q.isdigit():
            row = conn.execute(text("SELECT id, username FROM users WHERE id=:id LIMIT 1"),
                               {"id": int(q)}).mappings().first()
        else:
            row = conn.execute(text("SELECT id, username FROM users WHERE username ILIKE :u ORDER BY id LIMIT 1"),
                               {"u": q}).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="user_not_found")

    return {"ok": True, "id": int(row["id"]), "username": row["username"]}
