# backend/app/services/resumes.py
from __future__ import annotations
from typing import Iterable
from sqlalchemy import text

def upsert_resumes(SessionLocal, tg_id: int, items_like) -> int:
    """
    Сохраняем резюме пользователя в таблицу resumes.
    items_like — либо список элементов HH, либо dict с ключом 'items'.
    Возвращает кол-во обработанных.
    """
    if isinstance(items_like, dict):
        items = items_like.get("items", []) or []
    else:
        items = items_like or []

    if not items:
        return 0

    with SessionLocal() as db:
        uid = db.execute(text("SELECT id FROM users WHERE tg_id=:tg LIMIT 1"), {"tg": tg_id}).scalar()
        if uid is None:
            return 0

        n = 0
        for it in items:
            rid = str(it.get("id") or "").strip()
            if not rid:
                continue
            title = it.get("title")
            area = (it.get("area") or {}).get("name")
            updated_at = it.get("updated_at")
            visible = bool(it.get("visible", True))

            db.execute(
                text("""
                    INSERT INTO resumes (user_id, resume_id, title, area, updated_at, visible)
                    VALUES (:uid, :rid, :title, :area, :upd, :vis)
                    ON CONFLICT (resume_id) DO UPDATE
                      SET title = EXCLUDED.title,
                          area = EXCLUDED.area,
                          updated_at = EXCLUDED.updated_at,
                          visible = EXCLUDED.visible
                """),
                {"uid": int(uid), "rid": rid, "title": title, "area": area, "upd": updated_at, "vis": visible},
            )
            n += 1

        db.commit()
        return n
