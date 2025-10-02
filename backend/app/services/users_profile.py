# app/services/users_profile.py
from datetime import datetime, timezone
from sqlalchemy import text
from sqlalchemy.orm import Session

def save_hh_account_info(db: Session, tg_id: int, me: dict) -> None:
    """
    Сохраняем id и ФИО из HH в users.hh_account_id, users.hh_account_name.
    """
    hh_id = str(me.get("id") or "")
    first = (me.get("first_name") or "").strip()
    last = (me.get("last_name") or "").strip()
    full_name = " ".join(p for p in [first, last] if p)

    db.execute(
        text("""
        UPDATE users
           SET hh_account_id   = :hh_id,
               hh_account_name = :full_name,
               hh_expires_at   = :ts
         WHERE tg_id = :tg
        """),
        {
            "hh_id": hh_id,
            "full_name": full_name,
            "ts": datetime.now(timezone.utc),
            "tg": tg_id,
        },
    )
    db.commit()
