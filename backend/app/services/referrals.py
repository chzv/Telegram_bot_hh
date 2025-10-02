# app/services/referrals.py
from __future__ import annotations
from typing import Optional, List
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

async def get_user_by_ref_code(conn: AsyncConnection, code: str) -> Optional[int]:
    row = (await conn.execute(text("SELECT id FROM users WHERE ref_code = :c"), {"c": code})).scalar_one_or_none()
    return int(row) if row is not None else None

async def get_uplines(conn: AsyncConnection, user_id: int, max_levels: int = 3) -> List[int]:
    uplines: List[Optional[int]] = []
    cur = user_id
    for _ in range(max_levels):
        row = (await conn.execute(text("SELECT referred_by FROM users WHERE id=:id"), {"id": cur})).scalar_one_or_none()
        if row is None:
            uplines.append(None)
            cur = None
        else:
            uplines.append(int(row) if row is not None else None)
            cur = int(row) if row is not None else None
        if cur is None:
            uplines.extend([None] * (max_levels - len(uplines)))
            break
    return uplines[:max_levels]

async def ensure_ref_code(conn: AsyncConnection, user_id: int) -> str:
    row = (await conn.execute(text("SELECT ref_code FROM users WHERE id=:id"), {"id": user_id})).scalar_one_or_none()
    if row:
        return row
    import secrets, string
    abc = string.ascii_uppercase + string.digits
    while True:
        code = "".join(secrets.choice(abc) for _ in range(8))
        exists = (await conn.execute(text("SELECT 1 FROM users WHERE ref_code=:c"), {"c": code})).first()
        if not exists:
            await conn.execute(text("UPDATE users SET ref_code=:c WHERE id=:id"), {"c": code, "id": user_id})
            return code

def attach_pending_ref_on_link_sync(conn, user_id: int) -> bool:
    """
    Один раз привязывает пользователя к аплайну по users.ref и строит уровни 1..3
    в таблице referrals. Идемпотентно (ON CONFLICT DO NOTHING).
    Возвращает True, если что-то создали.
    """

    u = conn.execute(text("""
        SELECT id, ref, referred_by
        FROM users
        WHERE id=:uid
        FOR UPDATE
    """), {"uid": int(user_id)}).mappings().first()
    if not u:
        return False
    if u["referred_by"]:
        return False

    ref_code = (u["ref"] or "").strip()
    if not ref_code:
        return False

    parent = conn.execute(text("""
        SELECT id FROM users WHERE ref_code=:code LIMIT 1
    """), {"code": ref_code}).first()
    if not parent:
        return False
    parent_id = int(parent[0])
    if parent_id == int(user_id):
        return False

    conn.execute(text("""
        UPDATE users
           SET referred_by = :pid
         WHERE id=:uid AND referred_by IS NULL
    """), {"uid": int(user_id), "pid": parent_id})

    # уровень 1
    conn.execute(text("""
        INSERT INTO referrals (user_id, parent_user_id, level)
        VALUES (:uid, :pid, 1)
        ON CONFLICT (user_id, parent_user_id, level) DO NOTHING
    """), {"uid": int(user_id), "pid": parent_id})

    # уровни 2 и 3 — по цепочке referred_by
    lvl2 = conn.execute(text("SELECT referred_by FROM users WHERE id=:id"), {"id": parent_id}).scalar()
    if lvl2:
        conn.execute(text("""
            INSERT INTO referrals (user_id, parent_user_id, level)
            VALUES (:uid, :pid2, 2)
            ON CONFLICT (user_id, parent_user_id, level) DO NOTHING
        """), {"uid": int(user_id), "pid2": int(lvl2)})

        lvl3 = conn.execute(text("SELECT referred_by FROM users WHERE id=:id"), {"id": int(lvl2)}).scalar()
        if lvl3:
            conn.execute(text("""
                INSERT INTO referrals (user_id, parent_user_id, level)
                VALUES (:uid, :pid3, 3)
                ON CONFLICT (user_id, parent_user_id, level) DO NOTHING
            """), {"uid": int(user_id), "pid3": int(lvl3)})

    return True
