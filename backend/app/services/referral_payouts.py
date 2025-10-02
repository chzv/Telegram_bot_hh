# backend/app/services/referral_payouts.py
from __future__ import annotations
from typing import Dict, Tuple
from sqlalchemy import text
from sqlalchemy.orm import Session

DEFAULT_PCT = (20.0, 10.0, 5.0)  # L1,L2,L3 по умолчанию (в процентах)

def _get_tariff_percents(session: Session, tariff_id: int) -> Tuple[float, float, float]:
    """
    Возвращаем проценты L1..L3 для тарифа.
    Поддерживаем новые поля (ref_percent_l1/l2) и старые permille-поля.
    """
    row = session.execute(text("""
        SELECT
            -- новые проценты
            COALESCE(ref_percent_l1, NULL) AS p1_pct,
            COALESCE(ref_percent_l2, NULL) AS p2_pct,
            -- старые промилле (если вдруг есть)
            COALESCE(ref_p1_permille, NULL) AS p1_perm,
            COALESCE(ref_p2_permille, NULL) AS p2_perm,
            COALESCE(ref_p3_permille, NULL) AS p3_perm
        FROM tariffs
        WHERE id = :tid OR nemiling_tarif_id = :tid
        ORDER BY CASE WHEN id = :tid THEN 0 ELSE 1 END
        LIMIT 1
    """), {"tid": int(tariff_id)}).mappings().first()

    if not row:
        return DEFAULT_PCT

    # приоритет – проценты; если их нет, конвертим из промилле
    if row["p1_pct"] is not None or row["p2_pct"] is not None:
        l1 = float(row["p1_pct"] or 0) or DEFAULT_PCT[0]
        l2 = float(row["p2_pct"] or 0) or DEFAULT_PCT[1]
        l3 = DEFAULT_PCT[2]
        return (l1, l2, l3)

    # фолбэк: из permille -> проценты
    p1 = float(row["p1_perm"] or 0.0) / 10.0
    p2 = float(row["p2_perm"] or 0.0) / 10.0
    p3 = float(row["p3_perm"] or 0.0) / 10.0
    l1 = p1 or DEFAULT_PCT[0]
    l2 = p2 or DEFAULT_PCT[1]
    l3 = p3 or DEFAULT_PCT[2]
    return (l1, l2, l3)

def _uplines(session: Session, user_id: int) -> Dict[int, int]:
    """
    Возвращает {1: parent_id, 2: ..., 3: ...}.
    Сначала пробуем из referrals, иначе — по users.referred_by.
    """
    res = {1: None, 2: None, 3: None}

    # 1) из таблицы referrals
    rows = session.execute(text("""
        SELECT level, parent_user_id
        FROM referrals
        WHERE user_id = :uid AND level IN (1,2,3)
    """), {"uid": int(user_id)}).all()
    for lvl, pid in rows:
        if pid and 1 <= int(lvl) <= 3:
            res[int(lvl)] = int(pid)

    # 2) если пусто — цепочка по users.referred_by
    if not any(res.values()):
        lvl1 = session.execute(text("SELECT referred_by FROM users WHERE id=:id"), {"id": int(user_id)}).scalar()
        if lvl1:
            res[1] = int(lvl1)
            lvl2 = session.execute(text("SELECT referred_by FROM users WHERE id=:id"), {"id": int(lvl1)}).scalar()
            if lvl2:
                res[2] = int(lvl2)
                lvl3 = session.execute(text("SELECT referred_by FROM users WHERE id=:id"), {"id": int(lvl2)}).scalar()
                if lvl3:
                    res[3] = int(lvl3)

    return {k: v for k, v in res.items() if v}

def _add_balance(session: Session, user_id: int, amount_cents: int) -> None:
    session.execute(text("""
        INSERT INTO referral_balances (user_id, balance_cents)
        VALUES (:u, :a)
        ON CONFLICT (user_id)
        DO UPDATE SET balance_cents = referral_balances.balance_cents + EXCLUDED.balance_cents
    """), {"u": int(user_id), "a": int(amount_cents)})

def _add_trx(session: Session, user_id: int, amount_cents: int, kind: str, related_user_id: int) -> None:
    session.execute(text("""
        INSERT INTO referral_transactions (user_id, amount_cents, kind, related_user_id)
        VALUES (:u, :a, :k, :r)
    """), {"u": int(user_id), "a": int(amount_cents), "k": kind, "r": int(related_user_id)})

def payout_on_payment_sync(session: Session, payer_user_id: int, tariff_id: int, price_cents: int) -> int:
    """
    Начисляет бонусы L1..L3 за оплату payer_user_id.
    Возвращает кол-во созданных операций (для логов/диагностики).
    ИДЕМПОТЕНТНОСТЬ обеспечивается тем, что вызываем ТОЛЬКО при первом переходе платежа в 'paid'.
    """
    if not payer_user_id or not tariff_id or not price_cents:
        return 0

    l1, l2, l3 = _get_tariff_percents(session, tariff_id)
    perc = {1: l1, 2: l2, 3: l3}
    ups = _uplines(session, payer_user_id)
    created = 0

    for lvl, parent_id in ups.items():
        pct = float(perc.get(lvl, 0.0))
        if pct <= 0:
            continue
        reward = int(round(int(price_cents) * (pct / 100.0)))
        if reward <= 0:
            continue

        _add_trx(session, user_id=parent_id, amount_cents=reward, kind=f"bonus_l{lvl}", related_user_id=payer_user_id)
        _add_balance(session, user_id=parent_id, amount_cents=reward)
        created += 1

    return created
