from fastapi import APIRouter, Query
from pydantic import BaseModel
from app.services.dispatcher import dispatch_once
from ..deps import get_session


router = APIRouter(prefix="/hh/applications", tags=["hh"])

class DispatchOut(BaseModel):
    taken: int
    sent: int
    retried: int
    failed: int
    skipped: int

@router.post("/dispatch", response_model=DispatchOut)
async def dispatch(limit: int = Query(50, ge=1, le=500), dry_run: bool = Query(True)):
    stats = await dispatch_once(dry_run=dry_run, limit=limit)
    return DispatchOut(**stats)

from sqlalchemy import text

def log_application(session, user_id: int, resume_pk: int, vacancy_id: int,
                    status: str = "sent", source: str = "front_bot", kind: str = "manual"):
    session.execute(text("""
        INSERT INTO applications (user_id, resume_id, vacancy_id, status, source, kind, created_at, updated_at)
        VALUES (:uid, :rid_text, :vid, :st, :src, :kind, now(), now())
        ON CONFLICT (user_id, vacancy_id)
        DO UPDATE SET
            status     = EXCLUDED.status,
            source     = EXCLUDED.source,
            kind       = EXCLUDED.kind,
            updated_at = now()
    """), {
        "uid": user_id,
        "rid_text": str(resume_pk),
        "vid": vacancy_id,
        "st": status,          #  'queued|sent|error|retry'
        "src": source,
        "kind": kind,          # 'manual'/'auto'
    })

