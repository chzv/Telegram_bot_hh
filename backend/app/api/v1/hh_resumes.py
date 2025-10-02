# backend/app/api/v1/hh_resumes.py
from __future__ import annotations

from typing import Optional, TypedDict, List
import time
import requests

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

try:
    from app.db import SessionLocal  
except Exception: 
    from backend.app.db import SessionLocal  

try:
    from app.core.config import settings as _settings  
except Exception: 
    from backend.app.core.config import settings as _settings  

HH_API_BASE = getattr(_settings, "HH_API_BASE", "https://api.hh.ru")

router = APIRouter(prefix="/hh/resumes", tags=["hh_resumes"])

# ---------- helpers ----------

class TokenRow(TypedDict):
    user_id: int
    access_token: str
    exp: Optional[int]

def _get_user_id_by_tg(db, tg_id: int) -> Optional[int]:
    row = db.execute(
        text("SELECT id FROM users WHERE tg_id = :tg_id"),
        {"tg_id": tg_id},
    ).first()
    return int(row[0]) if row else None

def _get_tokens_by_tg(db, tg_id: int) -> Optional[TokenRow]:
    row = db.execute(
        text(
            """
            SELECT
                u.id          AS user_id,
                ht.access_token,
                EXTRACT(EPOCH FROM ht.expires_at)::bigint AS exp
            FROM hh_tokens ht
            JOIN users u ON u.id = ht.user_id
            WHERE u.tg_id = :tg_id
            """
        ),
        {"tg_id": tg_id},
    ).mappings().first()

    if not row:
        return None

    return {
        "user_id": int(row["user_id"]),
        "access_token": row["access_token"],
        "exp": int(row["exp"]) if row.get("exp") is not None else None,
    }

def _upsert_resume(db, user_id: int, hh_resume_id: str, title: str, area_name: Optional[str]) -> None:
    db.execute(
        text(
            """
            INSERT INTO resumes (user_id, resume_id, title, area, updated_at)
            VALUES (:user_id, :resume_id, :title, :area, now())
            ON CONFLICT (user_id, resume_id) DO UPDATE
               SET title      = EXCLUDED.title,
                   area       = EXCLUDED.area,
                   updated_at = now()
            """
        ),
        {
            "user_id": user_id,
            "resume_id": hh_resume_id,   
            "title": title,
            "area": area_name,          
        },
    )


# ---------- роуты ----------

@router.post("/sync")
def sync_resumes(tg_id: int = Query(..., description="Telegram user id")):
    """
    Тянем резюме из HH и сохраняем/обновляем в таблицу resumes.
    """
    with SessionLocal() as db:
        tok = _get_tokens_by_tg(db, tg_id)
        if not tok:
            raise HTTPException(404, "no tokens")

        if tok["exp"] and tok["exp"] <= int(time.time()):
            raise HTTPException(401, "token expired, refresh required")

        r = requests.get(
            f"{HH_API_BASE.rstrip('/')}/resumes/mine",
            headers={"Authorization": f"Bearer {tok['access_token']}"},
            timeout=15,
        )
        if r.status_code != 200:
            raise HTTPException(r.status_code, r.text)

        data = r.json()
        items = data.get("items") or []

        saved = 0
        

        for it in items:
            hh_resume_id = str(it.get("id") or "").strip()
            if not hh_resume_id:
                continue

            title = (it.get("title") or "").strip()

            area_name = None
            try:
                area_name = (it.get("area") or {}).get("name")
                if area_name is not None:
                    area_name = str(area_name).strip()
            except Exception:
                area_name = None

            _upsert_resume(
                db,
                user_id=tok["user_id"],
                hh_resume_id=hh_resume_id,
                title=title,
                area_name=area_name,
            )
            saved += 1

        db.commit()

    return {"ok": True, "saved": saved}

@router.get("")
def list_resumes(tg_id: int = Query(..., description="Telegram user id")):
    with SessionLocal() as db:
        user_id = _get_user_id_by_tg(db, tg_id)
        if not user_id:
            return {"items": []}

        rows = db.execute(
            text(
                """
                SELECT id, resume_id, title, area, updated_at
                  FROM resumes
                 WHERE user_id = :uid
                 ORDER BY updated_at DESC NULLS LAST
                """
            ),
            {"uid": user_id},
        ).mappings().all()

        items: List[dict] = []
        for r in rows:
            items.append(
                {
                    "id": str(r["resume_id"]),
                    "db_id": int(r["id"]),
                    "title": r["title"],
                    "area": r["area"],
                    "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                }
            )

        return {"items": items}

@router.get("/{resume_id}")
def resume_stats(resume_id: int):
    """
    Профиль/статистика по одному резюме (по внутреннему PK).
    """
    with SessionLocal() as db:
        r = db.execute(
            text(
                """
                SELECT id, user_id, resume_id, title, area, created_at, updated_at
                  FROM resumes
                 WHERE id = :rid
                """
            ),
            {"rid": resume_id},
        ).mappings().first()

        if not r:
            raise HTTPException(404, "resume not found")

        return {
            "id": int(r["id"]),
            "user_id": int(r["user_id"]),
            "resume_id": r["resume_id"],
            "title": r["title"],
            "area": r["area"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        }
