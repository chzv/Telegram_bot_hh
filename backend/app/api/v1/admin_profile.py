# backend/app/api/v1/admin_profile.py
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import os, socket

router = APIRouter(prefix="/admin", tags=["admin"])

def _build_sync_dsn() -> str:
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        raise RuntimeError("No DATABASE_URL found")
    dsn = dsn.replace("postgresql+asyncpg://","postgresql+psycopg2://").replace("postgresql://","postgresql+psycopg2://",1)
    if not os.path.exists("/.dockerenv"):
        try:
            url = make_url(dsn)
            host = (url.host or "")
            socket.gethostbyname(host) 
        except Exception:
            pass
    return dsn

_engine = create_engine(_build_sync_dsn(), pool_pre_ping=True, future=True)


@router.get("/users")
def admin_users_list(
    search: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    where = ""
    params = {"limit": limit, "offset": offset}
    if search:
        where = """
            WHERE (u.username ILIKE :q OR u.email ILIKE :q OR CAST(u.tg_id AS TEXT) ILIKE :q)
        """
        params["q"] = f"%{search}%"

    sql = f"""
    WITH
    apps AS (
    SELECT user_id, COUNT(*) AS total
    FROM applications
    GROUP BY user_id
    ),
    autos AS (
    SELECT user_id,
            COUNT(*) AS total,
            BOOL_OR(active) AS any_active
    FROM auto_responses
    GROUP BY user_id
    ),
    subs AS (
    SELECT DISTINCT ON (s.user_id)
            s.user_id,
            s.expires_at,
            t.title AS tariff_title
    FROM subscriptions s
    LEFT JOIN tariffs t ON t.id = s.tariff_id
    WHERE s.status = 'active'
        AND s.started_at <= now()
        AND s.expires_at  >  now()
    ORDER BY s.user_id, s.expires_at DESC
    )
    SELECT
    u.id,
    u.tg_id,
    u.username,
    u.email,
    COALESCE(u.hh_account_name, u.username, u.tg_id::text) AS name,
    u.created_at   AS registered_at,
    u.last_seen_at AS last_activity,
    /* hh_connected вычисляем по наличию валидного токена */
    CASE
        WHEN ht.user_id IS NOT NULL
            AND (ht.expires_at IS NULL OR ht.expires_at > now())
        THEN TRUE ELSE FALSE
    END AS hh_connected,
    COALESCE(rb.balance_cents, 0)  AS balance_cents,
    COALESCE(a.total, 0)           AS applications_total,
    COALESCE(au.total, 0)          AS auto_responses_total,
    COALESCE(au.any_active, FALSE) AS auto_responses_active,
    COALESCE(s.tariff_title, '')   AS subscription_title,
    s.expires_at                   AS subscription_expires_at
    FROM users u
    LEFT JOIN referral_balances rb ON rb.user_id = u.id
    LEFT JOIN apps  a   ON a.user_id  = u.id
    LEFT JOIN autos au  ON au.user_id = u.id
    LEFT JOIN subs  s   ON s.user_id  = u.id
    LEFT JOIN hh_tokens ht ON ht.user_id = u.id
    {where}
    ORDER BY u.created_at DESC
    LIMIT :limit OFFSET :offset
    """

    with _engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
        total = conn.scalar(text(f"SELECT COUNT(*) FROM users u {where}"), params) or 0

    items = []
    for r in rows:
        d = dict(r)
        d["balance"] = (d.get("balance_cents", 0) or 0) / 100.0
        items.append(d)

    return {"ok": True, "total": int(total), "limit": limit, "offset": offset, "items": items}

def _has_column(conn, table: str, column: str) -> bool:
    row = conn.execute(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name=:t AND column_name=:c
        LIMIT 1
    """), {"t": table, "c": column}).first()
    return bool(row)

def _resolve_tariff_id(conn, plan: Optional[str], tariff_id: Optional[int]) -> int:
    if tariff_id is not None:
        row = conn.execute(text(
            "SELECT id FROM tariffs WHERE id=:id AND is_active IS TRUE"
        ), {"id": tariff_id}).first()
        if row:
            return int(row[0])
        raise HTTPException(400, f"Неизвестный tariff_id: {tariff_id}")

    p = (plan or "").strip()
    if not p:
        raise HTTPException(400, "plan or tariff_id is required")

    row = conn.execute(text(
        "SELECT id FROM tariffs WHERE code=:code AND is_active IS TRUE"
    ), {"code": p}).first()
    if row:
        return int(row[0])
    raise HTTPException(400, f"Неизвестный plan: {plan}")

def _fetch_subscription(conn, user_id: int) -> dict:
    has_status     = _has_column(conn, "subscriptions", "status")
    has_is_active  = _has_column(conn, "subscriptions", "is_active")
    has_tariff_id  = _has_column(conn, "subscriptions", "tariff_id")
    has_tariff_code= _has_column(conn, "subscriptions", "tariff_code")
    if not (_has_column(conn, "subscriptions", "expires_at")):
        return {"plan": "free", "status": "inactive", "expires_at": None}

    join_sql = ""
    plan_sql = "s.tariff_code"
    if has_tariff_id:
        join_sql = "LEFT JOIN tariffs t ON t.id = s.tariff_id"
        plan_sql = "COALESCE(t.code, t.title, 'free')"
    elif has_tariff_code:
        join_sql = "LEFT JOIN tariffs t ON t.code = s.tariff_code"
        plan_sql = "COALESCE(s.tariff_code, t.code, 'free')"

    status_cond = "TRUE"
    if has_status:
        status_cond = "(s.status = 'active')"
    elif has_is_active:
        status_cond = "s.is_active"

    row = conn.execute(text("""
        SELECT
          COALESCE(t.code, 'free') AS plan,
          CASE WHEN s.status='active'
                AND COALESCE(s.started_at, now() - interval '100y') <= now()
                AND COALESCE(s.expires_at, now() - interval '100y')  > now()
               THEN 'active' ELSE 'inactive' END  AS status,
          s.expires_at
        FROM subscriptions s
        LEFT JOIN tariffs t ON t.id = s.tariff_id
        WHERE s.user_id = :id
        ORDER BY s.expires_at DESC NULLS LAST
        LIMIT 1
    """), {"id": user_id}).mappings().first()
    return dict(row) if row else {"plan": "free", "status": "inactive", "expires_at": None}


def _ensure_admin_comments(conn) -> None:
    conn.execute(text("""
      CREATE TABLE IF NOT EXISTS admin_comments (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        author VARCHAR(64) NOT NULL DEFAULT 'admin',
        text TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
      )
    """))

@router.get("/users/{user_id}")
def admin_user_profile(user_id: int):
    try:
        with _engine.connect() as conn:
            u = conn.execute(text("""
                SELECT id, tg_id, username, email, created_at, last_seen_at,
                       COALESCE(utm_source,'')   AS utm_source,
                       COALESCE(utm_medium,'')   AS utm_medium,
                       COALESCE(utm_campaign,'') AS utm_campaign
                FROM users WHERE id=:id
            """), {"id": user_id}).mappings().first()
            if not u:
                raise HTTPException(404, "user not found")

            bal = conn.scalar(text("SELECT balance_cents FROM referral_balances WHERE user_id=:id"), {"id": user_id}) or 0
            hh_connected = bool(conn.scalar(text("""
                SELECT 1 FROM hh_tokens
                WHERE user_id=:id AND (expires_at IS NULL OR expires_at > now())
                LIMIT 1
            """), {"id": user_id}))

            sub = _fetch_subscription(conn, user_id)

            apps_total = conn.scalar(text("SELECT COUNT(*) FROM applications WHERE user_id=:id"), {"id": user_id}) or 0
            pay = conn.execute(text("""
                SELECT COALESCE(SUM(CASE WHEN status='paid' THEN amount ELSE 0 END),0) AS total_paid,
                       MAX(CASE WHEN status='paid' THEN created_at ELSE NULL END)     AS last_paid_at
                FROM payments WHERE user_id=:id
            """), {"id": user_id}).mappings().first() if _has_column(conn, "payments", "amount") else None

        return {
            "ok": True,
            "user": {**dict(u), "balance_cents": int(bal), "hh_connected": hh_connected},
            "subscription": sub,
            "stats": {
                "applications_total": int(apps_total),
                "payments_total": float(pay["total_paid"] if pay else 0),
                "last_payment_at": (pay["last_paid_at"].isoformat() if pay and pay["last_paid_at"] else None),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"user profile error: {e}")

# ========== ОПЕРАЦИИ (без note/amount, авто-детект) ==========

@router.get("/users/{user_id}/operations")
def user_operations(user_id: int, limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0)):
    try:
        with _engine.connect() as conn:
            if _has_column(conn, "payments", "amount"):
                total = conn.scalar(text("SELECT COUNT(*) FROM payments WHERE user_id=:id"), {"id": user_id}) or 0
                rows = conn.execute(text("""
                    SELECT id, amount, status, provider, created_at
                    FROM payments
                    WHERE user_id=:id
                    ORDER BY created_at DESC
                    LIMIT :limit OFFSET :offset
                """), {"id": user_id, "limit": limit, "offset": offset}).mappings().all()
            else:
                total = conn.scalar(text("SELECT COUNT(*) FROM referral_transactions WHERE user_id=:id"), {"id": user_id}) or 0
                rows = conn.execute(text("""
                    SELECT id,
                           (amount_cents/100.0) AS amount,
                           'success'            AS status,
                           'referral'           AS provider,
                           created_at
                    FROM referral_transactions
                    WHERE user_id=:id
                    ORDER BY created_at DESC
                    LIMIT :limit OFFSET :offset
                """), {"id": user_id, "limit": limit, "offset": offset}).mappings().all()
        return {"ok": True, "total": int(total), "limit": limit, "offset": offset, "items": [dict(r) for r in rows]}
    except Exception as e:
        raise HTTPException(500, f"user operations error: {e}")

# ========== РЕФЕРАЛЫ (чтобы не было 404 и «демо 2») ==========

@router.get("/users/{user_id}/referrals")
def user_referrals(user_id: int):
    try:
        with _engine.connect() as conn:
            if _has_column(conn, "referrals", "parent_user_id"):
                rows = conn.execute(text("""
                    SELECT r.id, r.level, r.created_at,
                           r.user_id             AS target_id,
                           u2.username           AS target_username,
                           u2.tg_id              AS target_tg_id
                    FROM referrals r
                    LEFT JOIN users u2 ON u2.id = r.user_id
                    WHERE r.parent_user_id=:id
                    ORDER BY r.created_at DESC
                """), {"id": user_id}).mappings().all()
            else:
                rows = conn.execute(text("""
                    SELECT r.id, r.level, r.created_at,
                           r.referred_user_id    AS target_id,
                           u2.username           AS target_username,
                           u2.tg_id              AS target_tg_id
                    FROM referrals r
                    LEFT JOIN users u2 ON u2.id = r.referred_user_id
                    WHERE r.user_id=:id
                    ORDER BY r.created_at DESC
                """), {"id": user_id}).mappings().all()

            total = len(rows)
            lvl2 = sum(1 for r in rows if int(r["level"] or 0) == 2)
            lvl1 = sum(1 for r in rows if int(r["level"] or 0) == 1)

        return {"ok": True, "stats": {"total": total, "level1": lvl1, "level2": lvl2}, "items": [dict(r) for r in rows]}
    except Exception as e:
        raise HTTPException(500, f"user referrals error: {e}")

# ========== ПАТЧ ПОЛЬЗОВАТЕЛЯ + ПОДПИСКА (универсально) ==========

class SubscriptionPatch(BaseModel):
    plan: Optional[str] = None           # код тарифа (например, 'week', 'month')
    tariff_id: Optional[int] = None       # 'week' / 'month' / 'basic' / 'pro' / код тарифа
    status: Optional[str] = None      # 'active' / 'inactive'
    expires_at: Optional[datetime] = None

class AdminUserPatch(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    status: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    subscription: Optional[SubscriptionPatch] = None

def _upsert_subscription(conn, user_id, plan, status, expires_at, tariff_id):
    if tariff_id is None and plan:
        tariff_id = conn.execute(
            text("SELECT id FROM tariffs WHERE code = :code LIMIT 1"),
            {"code": str(plan).strip().lower()},
        ).scalar()

    last = conn.execute(
        text("""
            SELECT id, tariff_id, expires_at
              FROM subscriptions
             WHERE user_id = :uid
          ORDER BY id DESC
             LIMIT 1
        """),
        {"uid": user_id},
    ).mappings().first()

    if last:
        conn.execute(
            text("""
                UPDATE subscriptions
                   SET tariff_id = COALESCE(:tid, tariff_id),
                       status    = COALESCE(:st,  status),
                       expires_at= COALESCE(:exp,  expires_at)
                 WHERE id = :sid
            """),
            {
                "tid": tariff_id,         
                "st":  status,            
                "exp": expires_at,         
                "sid": last["id"],
            },
        )
        return

    conn.execute(
        text("""
            INSERT INTO subscriptions (user_id, tariff_id, expires_at, status)
            VALUES (:uid, :tid, COALESCE(:exp, NOW()), COALESCE(:st, 'inactive'))
        """),
        {
            "uid": user_id,
            "tid": tariff_id,
            "exp": expires_at,            # None -> NOW()
            "st":  status,
        },
    )

@router.patch("/users/{user_id}")
def admin_update_user(user_id: int, body: AdminUserPatch):
    payload = body.dict(exclude_unset=True)
    with _engine.begin() as conn:
        if not conn.scalar(text("SELECT 1 FROM users WHERE id=:id"), {"id": user_id}):
            raise HTTPException(404, "user not found")

        sub_in = payload.pop("subscription", None)
        if payload:
            set_sql = ", ".join(f"{k} = :{k}" for k in payload.keys())
            payload["id"] = user_id
            conn.execute(text(f"UPDATE users SET {set_sql} WHERE id=:id"), payload)
        pay = None
        if _has_column(conn, "payments", "amount"):
            pay = conn.execute(text("""
                SELECT COALESCE(SUM(CASE WHEN status='paid' THEN amount ELSE 0 END),0) AS total_paid,
                    MAX(CASE WHEN status='paid' THEN created_at ELSE NULL END)     AS last_paid_at
                FROM payments WHERE user_id=:id
            """), {"id": user_id}).mappings().first()

        if sub_in is not None:
            sp = SubscriptionPatch(**sub_in)
            _upsert_subscription(conn, user_id, sp.plan, sp.status, sp.expires_at, sp.tariff_id)
            sub = _fetch_subscription(conn, user_id)
        else:
            sub = _fetch_subscription(conn, user_id)

        # собрать свежие данные профиля
        u = conn.execute(text("""
            SELECT id, tg_id, username, email, created_at, last_seen_at,
                   COALESCE(utm_source,'') AS utm_source,
                   COALESCE(utm_medium,'') AS utm_medium,
                   COALESCE(utm_campaign,'') AS utm_campaign
            FROM users WHERE id=:id
        """), {"id": user_id}).mappings().first()
        sub = _fetch_subscription(conn, user_id)
        apps_total = conn.scalar(text("SELECT COUNT(*) FROM applications WHERE user_id=:id"), {"id": user_id}) or 0

        pay_total, last_paid = 0.0, None
        if _has_column(conn, "payments", "amount"):
            row = conn.execute(text("""
                SELECT COALESCE(SUM(CASE WHEN status='paid' THEN amount ELSE 0 END),0) AS total_paid,
                       MAX(CASE WHEN status='paid' THEN created_at ELSE NULL END)     AS last_paid_at
                FROM payments WHERE user_id=:id
            """), {"id": user_id}).mappings().first()
            pay_total = float(row["total_paid"] or 0)
            last_paid = row["last_paid_at"].isoformat() if row["last_paid_at"] else None

    return {
        "ok": True, "user": dict(u), "subscription": sub,
        "stats": {"applications_total": int(apps_total),
                  "payments_total": pay_total, "last_payment_at": last_paid}
    }

def _ensure_admin_comments(conn) -> None:
    conn.execute(text("""
      CREATE TABLE IF NOT EXISTS admin_comments (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        author VARCHAR(64) NOT NULL DEFAULT 'admin',
        text TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
      )
    """))

# ---------- APPLICATIONS (robust columns) ----------
@router.get("/users/{user_id}/applications")
def user_applications(user_id: int, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    try:
        with _engine.connect() as conn:
            cols = {r["column_name"] for r in conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='applications'
            """)).mappings().all()}

            # title column autodetect
            title_candidates = []
            for c in ("vacancy_title", "title", "position", "vacancy", "job_title"):
                if c in cols:
                    title_candidates.append(c)
            if not title_candidates:
                title_sql = "''::text"
            elif len(title_candidates) == 1:
                title_sql = title_candidates[0]
            else:
                title_sql = "COALESCE(" + ", ".join(title_candidates) + ")"

            # status column autodetect
            status_col = "status" if "status" in cols else ("state" if "state" in cols else None)
            status_sql = status_col if status_col else "NULL::text"

            # created_at autodetect
            created_col = "created_at" if "created_at" in cols else ("created" if "created" in cols else ("ts" if "ts" in cols else None))
            created_sql = (created_col + " AS created_at") if created_col else "NULL::timestamp AS created_at"
            order_col = created_col or "id"

            total = conn.scalar(text("SELECT COUNT(*) FROM applications WHERE user_id=:id"), {"id": user_id}) or 0
            rows = conn.execute(text(f"""
                SELECT id, {title_sql} AS title, {status_sql} AS status, {created_sql}
                FROM applications
                WHERE user_id=:id
                ORDER BY {order_col} DESC
                LIMIT :limit OFFSET :offset
            """), {"id": user_id, "limit": limit, "offset": offset}).mappings().all()

        return {"ok": True, "total": int(total), "limit": limit, "offset": offset, "items": [dict(r) for r in rows]}
    except Exception as e:
        raise HTTPException(500, f"user applications error: {e}")


# ---------- PATCH ----------

class AdminUserPatch(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    status: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    subscription: Optional[SubscriptionPatch] = None

@router.post("/users/{user_id}/balance")
def admin_user_balance_topup(user_id: int, payload: dict):
    """
    Пополнение/списание реферального баланса.
    Тело запроса:
      { "amount": 123.45 }  # рубли, можно отрицательное для списания
      или { "amount_cents": 12345 }  # копейки
      необязательное: { "note": "причина/комментарий" }
    """
    try:
        if payload is None:
            raise HTTPException(status_code=400, detail="Empty payload")

        amount_cents = None
        if "amount_cents" in payload:
            try:
                amount_cents = int(payload.get("amount_cents"))
            except Exception:
                raise HTTPException(status_code=400, detail="amount_cents must be integer")
        elif "amount" in payload:
            # amount в рублях -> в копейки
            try:
                raw = float(str(payload.get("amount")).replace(",", "."))
                amount_cents = int(round(raw * 100))
            except Exception:
                raise HTTPException(status_code=400, detail="amount must be number")
        else:
            raise HTTPException(status_code=400, detail="amount or amount_cents is required")

        if amount_cents == 0:
            raise HTTPException(status_code=400, detail="amount cannot be zero")

        note = (payload.get("note") or "").strip()[:500] if isinstance(payload.get("note"), str) else ""

        with _engine.begin() as conn:
            exists = conn.scalar(text("SELECT 1 FROM users WHERE id = :id"), {"id": user_id})
            if not exists:
                raise HTTPException(status_code=404, detail=f"User {user_id} not found")

            conn.execute(
                text("""
                    INSERT INTO referral_balances (user_id, balance_cents)
                    VALUES (:id, :delta)
                    ON CONFLICT (user_id) DO UPDATE
                    SET balance_cents = referral_balances.balance_cents + EXCLUDED.balance_cents
                """),
                {"id": user_id, "delta": amount_cents},
            )

            # запись операции
            conn.execute(
                text("""
                    INSERT INTO referral_transactions (user_id, amount_cents, kind, related_user_id)
                    VALUES (:id, :delta, :kind, NULL)
                """),
                {
                    "id": user_id,
                    "delta": amount_cents,
                    "kind": "manual_topup" if amount_cents > 0 else "manual_withdraw",
                },
            )

            # читаем новый баланс
            new_balance_cents = conn.scalar(
                text("SELECT balance_cents FROM referral_balances WHERE user_id=:id"),
                {"id": user_id},
            ) or 0

        return {
            "ok": True,
            "user_id": user_id,
            "balance_cents": int(new_balance_cents),
            "balance": float(round(new_balance_cents / 100, 2)),
            "note": note,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"balance update error: {e}")

class AdminCommentIn(BaseModel):
    text: str | None = None
    note: str | None = None

@router.get("/users/{user_id}/comments")
def admin_get_comments(user_id: int, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    with _engine.begin() as conn:
        _ensure_admin_comments(conn)
        rows = conn.execute(text("""
            SELECT id, user_id, COALESCE(author,'Администратор') AS author, text, created_at
            FROM admin_comments
            WHERE user_id=:uid
            ORDER BY created_at DESC
            LIMIT :lim OFFSET :off
        """), {"uid": user_id, "lim": limit, "off": offset}).mappings().all()
        total = conn.scalar(text("SELECT COUNT(*) FROM admin_comments WHERE user_id=:uid"), {"uid": user_id}) or 0
    return {"ok": True, "total": int(total), "limit": limit, "offset": offset, "items": [dict(r) for r in rows]}

@router.post("/users/{user_id}/comments")
def admin_add_comment(user_id: int, body: AdminCommentIn):
    text_val = ((body.text or "") or (body.note or "")).strip()
    if not text_val:
        raise HTTPException(status_code=400, detail="text is required")
    with _engine.begin() as conn:
        _ensure_admin_comments(conn)
        conn.execute(text("""
            INSERT INTO admin_comments(user_id, author, text)
            VALUES (:uid, 'Администратор', :tx)
        """), {"uid": user_id, "tx": text_val})
    return {"ok": True}

@router.get("/users/{user_id}/notes")
def admin_get_notes_alias(user_id: int, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    return admin_get_comments(user_id=user_id, limit=limit, offset=offset)

@router.post("/users/{user_id}/notes")
def admin_add_note_alias(user_id: int, body: AdminCommentIn):
    return admin_add_comment(user_id=user_id, body=body)

