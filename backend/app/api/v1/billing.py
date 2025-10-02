from fastapi import APIRouter, Depends
from pydantic import BaseModel, conint
from sqlalchemy import text
from ..deps import get_session


router = APIRouter(prefix="/payments", tags=["billing"])

class Webhook(BaseModel):
    invoice_id: str
    status: str
    amount_cents: conint(ge=0)
    user_id: conint(gt=0)
    tariff_id: conint(gt=0)

@router.post("/confirm")
def payments_confirm(body: Webhook, session = Depends(get_session)):
    if body.status.lower() != "paid":
        return {"ok": True, "ignored": True}
    uid = session.execute(text("SELECT id FROM users WHERE tg_user_id=:tg"), {"tg": int(body.user_id)}).scalar()
    if not uid:
        session.execute(text("INSERT INTO users (tg_user_id, username) VALUES (:tg, '')"), {"tg": int(body.user_id)})
        uid = session.execute(text("SELECT id FROM users WHERE tg_user_id=:tg"), {"tg": int(body.user_id)}).scalar()
    session.execute(text("""
        WITH t AS (SELECT period_days FROM tariffs WHERE id=:tid AND active)
        INSERT INTO subscriptions (user_id, tariff_id, active, started_at, expires_at)
        SELECT :uid, :tid, TRUE, NOW(), NOW() + (SELECT period_days FROM t) * INTERVAL '1 day'
        ON CONFLICT (user_id, active) WHERE active
        DO UPDATE SET tariff_id=:tid, started_at=NOW(), expires_at=EXCLUDED.expires_at
    """), {"uid": int(uid), "tid": int(body.tariff_id)})
    try:
        uid = session.execute(text("SELECT id FROM users WHERE tg_id=:tg"), {"tg": int(body.user_id)}).scalar()
        if not uid:
            session.execute(text("INSERT INTO users (tg_id, username) VALUES (:tg, '')"), {"tg": int(body.user_id)})
            uid = session.execute(text("SELECT id FROM users WHERE tg_id=:tg"), {"tg": int(body.user_id)}).scalar()

        # проценты из тарифа
        perc = session.execute(text("""
            SELECT COALESCE(ref_p1_permille,0), COALESCE(ref_p2_permille,0), COALESCE(ref_p3_permille,0)
            FROM tariffs WHERE id=:tid
        """), {"tid": int(body.tariff_id)}).first()
        p1, p2, p3 = (int(perc[0]) if perc else 0), (int(perc[1]) if perc else 0), (int(perc[2]) if perc else 0)

        if any([p1, p2, p3]):
            # цепочка аплайнов до 3 уровней
            rows = session.execute(text("""
                WITH RECURSIVE ups AS (
                  SELECT referred_by AS upline, 1 AS level FROM users WHERE id=:u
                  UNION ALL
                  SELECT users.referred_by, ups.level+1
                  FROM users JOIN ups ON users.id = ups.upline
                  WHERE ups.level < 3
                )
                SELECT upline, level FROM ups WHERE upline IS NOT NULL ORDER BY level
            """), {"u": int(uid)}).fetchall()

            perm = {1: p1, 2: p2, 3: p3}
            for r in rows:
                up_id, level = int(r[0]), int(r[1])
                pp = perm.get(level, 0)
                if pp <= 0:
                    continue
                amt = (int(body.amount_cents) * pp) // 1000
                if amt <= 0:
                    continue
                session.execute(text("""
                    INSERT INTO referral_events (user_id, upline_user_id, level, event, amount_cents, details)
                    VALUES (:u, :up, :lvl, 'payment', :amt, :det)
                """), {"u": int(uid), "up": up_id, "lvl": level, "amt": int(amt),
                       "det": f"tariff:{int(body.tariff_id)};invoice:{body.invoice_id}"})
        session.commit()
    except Exception:
        session.rollback()
    session.commit()
    return {"ok": True}
