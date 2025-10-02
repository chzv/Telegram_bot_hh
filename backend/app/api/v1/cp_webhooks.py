# app/api/v1/cp_webhooks.py
from __future__ import annotations

import base64, hmac, hashlib, json, logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from app.core.config import CP_API_SECRET
from app.db import SessionLocal
from app.services.referral_payouts import payout_on_payment_sync 

router = APIRouter(prefix="/cp", tags=["payments"])


# ---------- utils ----------

def _ok() -> dict:
    return {"code": 0}

def _get_sig_header(headers) -> str:
    cand = (
        headers.get("Content-HMAC")
        or headers.get("Content-Hmac")
        or headers.get("X-Content-HMAC")
        or headers.get("X-Content-Hmac")
        or ""
    )
    return cand

def _verify_hmac(raw: bytes, hdr_b64: str) -> bool:
    if not hdr_b64:
        return False
    mac = hmac.new(CP_API_SECRET.encode("utf-8"), raw, hashlib.sha256).digest()
    good = base64.b64encode(mac).decode("utf-8")
    return good == hdr_b64

def _parse_cp_payload(raw: bytes, content_type: str) -> dict:
    ct = (content_type or "").lower().split(";")[0].strip()
    if ct == "application/json":
        return json.loads(raw.decode("utf-8"))
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        pass
    pairs = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
    p = {k: (v[0] if isinstance(v, list) and v else "") for k, v in pairs.items()}
    if isinstance(p.get("Data"), str):
        try:
            p["Data"] = json.loads(p["Data"])
        except Exception:
            pass
    return p

def _as_int(x, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        try:
            return int(float(x))
        except Exception:
            return default

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------- endpoints ----------

@router.post("/check")
async def cp_check(request: Request):
    raw = await request.body()
    if not _verify_hmac(raw, _get_sig_header(request.headers)):
        raise HTTPException(403, "bad signature")
    return _ok()


@router.post("/pay")
async def cp_pay(request: Request):
    raw = await request.body()
    if not _verify_hmac(raw, _get_sig_header(request.headers)):
        raise HTTPException(403, "bad signature")

    try:
        p = _parse_cp_payload(raw, request.headers.get("Content-Type", ""))
    except Exception:
        logging.exception("cp_pay: cannot parse payload")
        return _ok()

    txn_id = str(p.get("TransactionId") or p.get("InvoiceId") or "").strip()
    data = p.get("Data") or {}
    if not isinstance(data, dict):
        data = {}

    plan_code = (data.get("plan") or "").strip() or "month"
    tg_id = _as_int(p.get("AccountId") or data.get("tg_id") or 0, 0)
    amount_cents = _as_int(round(float(p.get("Amount") or 0.0) * 100), 0)
    now = _now_utc()

    if not tg_id or not txn_id:
        logging.warning("cp_pay: missing tg_id or transaction id: %s", {"tg_id": tg_id, "txn": txn_id})
        return _ok()

    with SessionLocal() as db:
        # user
        user_row = db.execute(text("SELECT id FROM users WHERE tg_id=:tg"), {"tg": tg_id}).first()
        if not user_row:
            logging.warning("cp_pay: user not found for tg_id=%s", tg_id)
            return _ok()
        user_id = int(user_row[0])

        # tariff
        tariff = db.execute(text("""
            SELECT id, price_cents, period_days
            FROM tariffs
            WHERE code=:code AND COALESCE(is_active, TRUE)=TRUE
        """), {"code": plan_code}).first()
        if not tariff:
            logging.warning("cp_pay: tariff not found for code=%s", plan_code)
            return _ok()

        tariff_id, price_expected, period_days = int(tariff[0]), int(tariff[1]), int(tariff[2])
        amount_effective = amount_cents or price_expected  # на всякий случай

        # ---------- идемпотентность платежа ---------
        exist = db.execute(text("""
            SELECT id, status
            FROM payments
            WHERE provider='cloudpayments' AND provider_id=:pid
            FOR UPDATE
        """), {"pid": txn_id}).mappings().first()

        did_pay_now = False  

        if not exist:
            db.execute(
                text("""
                    INSERT INTO payments (
                      user_id, provider, provider_id, tariff_id, amount_cents, status, raw, description
                    ) VALUES (
                      :uid, 'cloudpayments', :pid, :tid, :amt, 'paid', :raw, :desc
                    )
                """).bindparams(bindparam("raw", type_=JSONB)),
                {"uid": user_id, "pid": txn_id, "tid": tariff_id,
                 "amt": amount_effective, "raw": p, "desc": f"CP {plan_code}"},
            )
            did_pay_now = True
        else:
            if (exist["status"] or "").lower() != "paid":
                db.execute(
                    text("""
                        UPDATE payments
                           SET status='paid',
                               user_id=:uid,
                               tariff_id=:tid,
                               amount_cents=:amt,
                               raw=:raw,
                               description=:desc
                         WHERE provider='cloudpayments' AND provider_id=:pid
                    """).bindparams(bindparam("raw", type_=JSONB)),
                    {"uid": user_id, "tid": tariff_id, "amt": amount_effective,
                     "raw": p, "desc": f"CP {plan_code}", "pid": txn_id},
                )
                did_pay_now = True
            else:
                return _ok()

        # ---------- только при первом переходе в paid ----------
        # продлеваем/создаём подписку
        sub = db.execute(text("""
            SELECT id, expires_at, status
            FROM subscriptions
            WHERE user_id=:uid AND status='active'
            ORDER BY expires_at DESC
            LIMIT 1
        """), {"uid": user_id}).first()

        base_from = now
        if sub and sub[1] and sub[2] == "active" and sub[1] > now:
            base_from = sub[1]
        new_until = base_from + timedelta(days=period_days)

        if sub:
            db.execute(text("""
                UPDATE subscriptions
                SET expires_at=:until
                WHERE id=:sid
            """), {"until": new_until, "sid": sub[0]})
        else:
            db.execute(text("""
                INSERT INTO subscriptions (user_id, tariff_id, started_at, expires_at, status, source)
                VALUES (:uid, :tid, :start, :until, 'active', 'cloudpayments')
            """), {"uid": user_id, "tid": tariff_id, "start": now, "until": new_until})

        # реферальные начисления — один раз при успешной оплате
        try:
            payout_on_payment_sync(db, user_id, tariff_id, price_expected)
        except Exception:
            logging.exception("cp_pay: referral payouts failed (user_id=%s, tariff_id=%s)", user_id, tariff_id)

        db.commit()

    return _ok()


@router.post("/fail")
async def cp_fail(request: Request):
    raw = await request.body()
    if not _verify_hmac(raw, _get_sig_header(request.headers)):
        raise HTTPException(403, "bad signature")

    try:
        p = _parse_cp_payload(raw, request.headers.get("Content-Type", ""))
    except Exception:
        p = {}

    txn_id = str(p.get("TransactionId") or p.get("InvoiceId") or "").strip()
    amount_cents = _as_int(round(float(p.get("Amount") or 0.0) * 100), 0)

    with SessionLocal() as db:
        db.execute(
            text("""
                INSERT INTO payments (provider, provider_id, amount_cents, status, raw, description)
                VALUES ('cloudpayments', :pid, :amt, 'failed', :raw, 'CP fail')
                ON CONFLICT (provider_id) DO UPDATE
                  SET status='failed', raw=EXCLUDED.raw
            """).bindparams(bindparam("raw", type_=JSONB)),
            {"pid": txn_id, "amt": amount_cents, "raw": p},
        )
        db.commit()

    return _ok()


@router.get("/_diag")
async def cp_diag():
    with SessionLocal() as db:
        dbinfo = db.execute(text("""
            SELECT current_database() AS db,
                   current_user      AS usr,
                   inet_server_addr()::text AS host,
                   inet_server_port()::int  AS port
        """)).mappings().first()

        tables = db.execute(text("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_name IN ('payments','subscriptions','users','tariffs')
            ORDER BY table_schema, table_name
        """)).mappings().all()

        counts = db.execute(text("""
            SELECT 'users' AS t, COUNT(*)::int AS c FROM users
            UNION ALL
            SELECT 'subscriptions' AS t, COUNT(*)::int FROM subscriptions
            UNION ALL
            SELECT 'payments' AS t, COUNT(*)::int FROM payments
            UNION ALL
            SELECT 'tariffs' AS t, COUNT(*)::int FROM tariffs
        """)).mappings().all()

    return {"db": dbinfo, "tables": tables, "counts": counts}
