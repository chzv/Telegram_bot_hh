# backend/app/api/v1/cover_letters.py
from __future__ import annotations

from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from app.core.config import settings

router = APIRouter(prefix="/cover-letters", tags=["cover_letters"])

# ── helpers ──────────────────────────────────────────────────────────────────
def _conn():
    eng = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    return eng.connect()

def _user_id_by_tg(conn, tg_id: int) -> Optional[int]:
    row = conn.execute(text("SELECT id FROM users WHERE tg_id=:tg"), {"tg": tg_id}).fetchone()
    return int(row[0]) if row else None

# ── models ───────────────────────────────────────────────────────────────────
class LetterOut(BaseModel):
    id: int
    title: str
    body: str

class LetterCreateIn(BaseModel):
    tg_id: int = Field(..., ge=1)
    title: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)

class LetterUpdateIn(BaseModel):
    tg_id: int = Field(..., ge=1)
    title: Optional[str] = None
    body: Optional[str] = None

# ── endpoints ────────────────────────────────────────────────────────────────
@router.get("", response_model=List[LetterOut])
def list_letters(tg_id: int = Query(..., ge=1)):
    with _conn() as conn:
        uid = _user_id_by_tg(conn, tg_id)
        if not uid:
            raise HTTPException(status_code=404, detail="user not found")
        rows = conn.execute(
            text("""
                SELECT id, title, body
                  FROM cover_letters
                 WHERE user_id=:uid
                 ORDER BY id DESC
            """),
            {"uid": uid}
        ).fetchall()
        return [{"id": int(r[0]), "title": r[1], "body": r[2]} for r in rows]

@router.post("", response_model=LetterOut)
def create_letter(payload: LetterCreateIn):
    with _conn() as conn:
        uid = _user_id_by_tg(conn, payload.tg_id)
        if not uid:
            raise HTTPException(status_code=404, detail="user not found")
        row = conn.execute(
            text("""
                INSERT INTO cover_letters(user_id, title, body)
                VALUES (:uid, :title, :body)
                RETURNING id, title, body
            """),
            {"uid": uid, "title": payload.title.strip(), "body": payload.body.strip()}
        ).fetchone()
        conn.commit()
        return {"id": int(row[0]), "title": row[1], "body": row[2]}

@router.put("/{letter_id}", response_model=LetterOut)
def update_letter(letter_id: int, payload: LetterUpdateIn):
    with _conn() as conn:
        uid = _user_id_by_tg(conn, payload.tg_id)
        if not uid:
            raise HTTPException(status_code=404, detail="user not found")
        row = conn.execute(
            text("""
                UPDATE cover_letters
                   SET title = COALESCE(:title, title),
                       body  = COALESCE(:body, body),
                       updated_at = now()
                 WHERE id=:id AND user_id=:uid
             RETURNING id, title, body
            """),
            {"id": letter_id, "uid": uid,
             "title": None if payload.title is None else payload.title.strip(),
             "body":  None if payload.body  is None else payload.body.strip()}
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        conn.commit()
        return {"id": int(row[0]), "title": row[1], "body": row[2]}

@router.delete("/{letter_id}")
def delete_letter(letter_id: int, tg_id: int = Query(..., ge=1)):
    with _conn() as conn:
        uid = _user_id_by_tg(conn, tg_id)
        if not uid:
            raise HTTPException(status_code=404, detail="user not found")
        res = conn.execute(
            text("DELETE FROM cover_letters WHERE id=:id AND user_id=:uid"),
            {"id": letter_id, "uid": uid}
        )
        conn.commit()
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="not found")
        return {"ok": True}
