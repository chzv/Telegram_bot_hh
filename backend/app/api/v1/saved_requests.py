from __future__ import annotations
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db import SessionLocal

router = APIRouter(prefix="/saved-requests", tags=["saved_requests"])

# ---- helpers ----
def _get_user_id_by_tg(db, tg_id: int) -> Optional[int]:
    row = db.execute(text("SELECT id FROM users WHERE tg_id=:tg_id"), {"tg_id": tg_id}).first()
    return int(row[0]) if row else None

class SavedRequestIn(BaseModel):
    title: str = Field(..., max_length=255)
    query: str = ""
    area: int | None = None
    employment: list[str] = []
    schedule: list[str] = []
    professional_roles: list[int] = []
    search_fields: list[str] = []
    cover_letter: str = ""
    query_params: str = ""
    resume: Optional[str] = None 

class SavedRequestOut(SavedRequestIn):
    id: int
    created_at: str
    updated_at: str

# ---- endpoints ----

@router.get("", response_model=list[SavedRequestOut])
def list_saved_requests(tg_id: int = Query(...)):
    with SessionLocal() as db:
        uid = _get_user_id_by_tg(db, tg_id)
        if not uid:
            return []

        rows = db.execute(
            text("""
                SELECT id, title, query, area, employment, schedule,
                       professional_roles, search_fields, cover_letter,
                       query_params, resume,             -- ← ДОБАВИЛИ
                       created_at, updated_at
                  FROM saved_requests
                 WHERE user_id = :uid
                 ORDER BY updated_at DESC
            """),
            {"uid": uid}
        ).mappings().all()

        out: list[dict[str, Any]] = []
        for r in rows:
            out.append({
                "id": int(r["id"]),
                "title": r["title"] or "",
                "query": r["query"] or "",
                "area": r["area"],
                "employment": r["employment"] or [],
                "schedule": r["schedule"] or [],
                "professional_roles": r["professional_roles"] or [],
                "search_fields": r["search_fields"] or [],
                "cover_letter": r["cover_letter"] or "",
                "query_params": r.get("query_params") or "",   
                "resume": r.get("resume"),                     
                "created_at": r["created_at"].isoformat(),
                "updated_at": r["updated_at"].isoformat(),
            })
        return out


@router.post("", response_model=SavedRequestOut)
def create_saved_request(payload: SavedRequestIn, tg_id: int = Query(...)):
    with SessionLocal() as db:
        uid = _get_user_id_by_tg(db, tg_id)
        if not uid:
            raise HTTPException(404, "user not found")

        row = db.execute(
            text("""
                INSERT INTO saved_requests
                    (user_id, title, query, area, employment, schedule,
                     professional_roles, search_fields, cover_letter,
                     query_params, resume,               -- ← ДОБАВИЛИ
                     created_at, updated_at)
                VALUES
                    (:uid, :title, :query, :area, :employment, :schedule,
                     :professional_roles, :search_fields, :cover_letter,
                     :query_params, :resume,            -- ← ДОБАВИЛИ
                     now(), now())
                RETURNING id, title, query, area, employment, schedule,
                          professional_roles, search_fields, cover_letter,
                          query_params, resume,           -- ← ДОБАВИЛИ
                          created_at, updated_at
            """),
            {
                "uid": uid,
                "title": payload.title,
                "query": payload.query,
                "area": payload.area,
                "employment": payload.employment,
                "schedule": payload.schedule,
                "professional_roles": payload.professional_roles,
                "search_fields": payload.search_fields,
                "cover_letter": payload.cover_letter,
                "query_params": payload.query_params or "",   
                "resume": payload.resume,                    
            },
        ).mappings().first()
        db.commit()

        return {
            "id": int(row["id"]),
            "title": row["title"] or "",
            "query": row["query"] or "",
            "area": row["area"],
            "employment": row["employment"] or [],
            "schedule": row["schedule"] or [],
            "professional_roles": row["professional_roles"] or [],
            "search_fields": row["search_fields"] or [],
            "cover_letter": row["cover_letter"] or "",
            "query_params": row.get("query_params") or "",   
            "resume": row.get("resume"),                     
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

@router.delete("/{req_id}")
def delete_saved_request(req_id: int, tg_id: int = Query(...)):
    with SessionLocal() as db:
        uid = _get_user_id_by_tg(db, tg_id)
        if not uid:
            raise HTTPException(404, "user not found")

        res = db.execute(
            text("DELETE FROM saved_requests WHERE id=:id AND user_id=:uid"),
            {"id": req_id, "uid": uid},
        )
        db.commit()
        if res.rowcount == 0:
            raise HTTPException(404, "not found")
        return {"ok": True}
