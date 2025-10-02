# backend/app/api/v1/admin_listings.py
from fastapi import APIRouter, Query, HTTPException
import os, socket, re, psycopg2
from psycopg2.extras import RealDictCursor
import csv, io
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/admin", tags=["admin"])


def _dsn_pg() -> str:
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        try:
            from app.core.config import settings
            dsn = (getattr(settings, "database_url", "") or "").strip()
        except Exception:
            pass
    if not dsn:
        raise RuntimeError("No DATABASE_URL found")

    # нормализуем схему
    if dsn.startswith("postgresql+psycopg2://"):
        dsn = dsn.replace("postgresql+psycopg2://", "postgresql://", 1)
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

    # локальная подмена хоста/порта, если нет docker-сети
    if not os.path.exists("/.dockerenv"):
        try:
            socket.getaddrinfo("db", 5432)
        except Exception:
            host = "localhost"
            port = os.getenv("PGPORT_HOST", "5433")
            dsn = re.sub(r"@db(?::\d+)?", f"@{host}:{port}", dsn, count=1)

    return dsn


# ====== USERS LIST (для таблицы «Пользователи») ======
@router.get("/users")
def list_users(
    q: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    Возвращает поля:
      id, tg_id, username, email,
      created_at, last_seen_at,
      hh_account_id, hh_account_name,
      name (вычисляемое), hh_connected (bool)
    """
    dsn = _dsn_pg()

    where = ""
    params = {"limit": limit, "offset": offset}
    if q:
        where = """
        WHERE (
            COALESCE(u.username,'') ILIKE %(q)s OR
            COALESCE(u.email,'')    ILIKE %(q)s OR
            COALESCE(u.hh_account_name,'') ILIKE %(q)s OR
            CAST(u.tg_id AS TEXT) ILIKE %(q)s
        )
        """
        params["q"] = f"%{q}%"

    sql = f"""
        SELECT
            u.id,
            u.tg_id,
            u.username,
            u.email,
            u.created_at,
            COALESCE(u.last_seen, u.created_at) AS last_seen_at,
            u.hh_account_id,
            u.hh_account_name,
            COALESCE(
                NULLIF(u.hh_account_name, ''),
                NULLIF(CONCAT_WS(' ', u.first_name, u.last_name), '')
            ) AS name,
            CASE
              WHEN ht.user_id IS NOT NULL
               AND (ht.expires_at IS NULL OR ht.expires_at > NOW())
              THEN TRUE ELSE FALSE
            END AS hh_connected,
            COALESCE(ps.subs_count_total, 0)::bigint          AS subs_count_total,
            COALESCE(pp.total_cents, 0)::bigint / 100.0       AS revenue_total_rub,
            COALESCE(rb.balance_cents, 0)::bigint / 100.0     AS referral_balance_rub,
            COALESCE(ap.applications_total, 0)::bigint        AS applications_total
        FROM users u
        LEFT JOIN hh_tokens ht ON ht.user_id = u.id
        LEFT JOIN (
          SELECT user_id, COUNT(*)::bigint AS subs_count_total
          FROM subscriptions
          GROUP BY user_id
        ) ps ON ps.user_id = u.id
        LEFT JOIN (
          SELECT user_id, SUM(amount_cents)::bigint AS total_cents
          FROM payments
          GROUP BY user_id
        ) pp ON pp.user_id = u.id
        LEFT JOIN (
          SELECT user_id, balance_cents
          FROM referral_balances
        ) rb ON rb.user_id = u.id
        LEFT JOIN (
          SELECT user_id, COUNT(*)::bigint AS applications_total
          FROM applications
          GROUP BY user_id
        ) ap ON ap.user_id = u.id
        {where}
        ORDER BY u.id DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """
    cnt = f"SELECT count(*) FROM users u {where}"

    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(cnt, params)
        total = int(cur.fetchone()["count"])
        cur.execute(sql, params)
        items = cur.fetchall()

    return {"ok": True, "items": items, "limit": limit, "offset": offset, "total": total}

# ====== USER PROFILE (страница/модалка профиля) ======
@router.get("/users/{user_id}")
def get_user(user_id: int):
    dsn = _dsn_pg()
    sql = """
        SELECT
            u.id, u.tg_id, u.username, u.email,
            u.first_name, u.last_name,
            u.utm_source, u.utm_medium, u.utm_campaign,
            u.created_at,
            COALESCE(u.last_seen, u.created_at) AS last_seen_at,
            u.hh_account_id, u.hh_account_name,
            CASE
              WHEN ht.user_id IS NOT NULL
               AND (ht.expires_at IS NULL OR ht.expires_at > NOW())
              THEN TRUE ELSE FALSE
            END AS hh_connected
        FROM users u
        LEFT JOIN hh_tokens ht ON ht.user_id = u.id
        WHERE u.id = %(id)s
        LIMIT 1
    """
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, {"id": user_id})
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "user not found")
    return {"ok": True, "item": row}


# ====== UPDATE USER (сохранение из модалки) ======
@router.post("/users/{user_id}/update")
def update_user(user_id: int, payload: dict):
    """
    Разрешённые поля: username, first_name, last_name, email,
    utm_source, utm_medium, utm_campaign.
    """
    allowed = {
        "username",
        "first_name",
        "last_name",
        "email",
        "utm_source",
        "utm_medium",
        "utm_campaign",
    }
    data = {k: v for k, v in (payload or {}).items() if k in allowed}
    if not data:
        return {"ok": True, "updated": 0}

    sets = ", ".join([f"{k} = %({k})s" for k in data.keys()])
    data["id"] = user_id

    dsn = _dsn_pg()
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE users SET {sets} WHERE id = %(id)s", data)
        conn.commit()

    return {"ok": True, "updated": 1}
    
@router.get("/export.csv")
def users_export_csv(
    q: str = "",
):
    """
    Полная выгрузка пользователей в CSV «на текущий момент» из БД (psycopg2):
    - все базовые поля, UTM (utm_source/utm_medium/utm_campaign),
    - агрегаты: subs_count_total, revenue_total_rub.
    """
    dsn = _dsn_pg()

    where = ""
    params = {}
    if q:
        where = """
        WHERE (
            COALESCE(u.username,'') ILIKE %(q)s OR
            COALESCE(u.email,'')    ILIKE %(q)s OR
            COALESCE(u.hh_account_name,'') ILIKE %(q)s OR
            CAST(u.tg_id AS TEXT) ILIKE %(q)s
        )
        """
        params["q"] = f"%{q}%"

    sql = f"""
        WITH subs AS (
          SELECT user_id, COUNT(*)::bigint AS subs_count_total
          FROM subscriptions
          GROUP BY user_id
        ),
        pays AS (
          SELECT user_id, SUM(amount_cents)::bigint AS total_cents
          FROM payments
          GROUP BY user_id
        )
        SELECT
          u.id,
          u.tg_id,
          u.username,
          u.first_name,
          u.last_name,
          u.email,
          u.created_at,
          COALESCE(u.last_seen, u.created_at) AS last_seen_at,
          u.is_premium,
          u.lang,
          u.ref_code,
          u.referred_by,
          u.utm_source,
          u.utm_medium,
          u.utm_campaign,
          u.hh_account_id,
          u.hh_account_name,
          COALESCE(s.subs_count_total, 0)::bigint        AS subs_count_total,
          COALESCE(p.total_cents, 0)::bigint / 100.0     AS revenue_total_rub
        FROM users u
        LEFT JOIN subs s ON s.user_id = u.id
        LEFT JOIN pays p ON p.user_id = u.id
        {where}
        ORDER BY u.id
    """

    def safe_cell(v):
        """CSV injection guard: экранируем формульные префиксы."""
        if v is None:
            return ""
        s = str(v)
        return ("'" + s) if s[:1] in ("=", "+", "-", "@") else s

    def generate_rows():
        with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            # ВАЖНО: заголовок должен соответствовать SELECT
            header = [
                "id","tg_id","username","first_name","last_name","email",
                "created_at","last_seen_at","is_premium","lang",
                "ref_code","referred_by",
                "utm_source","utm_medium","utm_campaign",
                "hh_account_id","hh_account_name",
                "subs_count_total","revenue_total_rub"
            ]
            # пишем в буфер построчно
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(header)
            yield buf.getvalue()
            buf.seek(0); buf.truncate(0)

            for row in cur:
                writer.writerow([safe_cell(row.get(k)) for k in header])
                yield buf.getvalue()
                buf.seek(0); buf.truncate(0)

    return StreamingResponse(
        generate_rows(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="users-export.csv"',
            "Cache-Control": "no-store",
        },
    )
