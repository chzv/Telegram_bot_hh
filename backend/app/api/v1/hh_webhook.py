# app/api/v1/hh_webhook.py 
from fastapi import APIRouter, Request, HTTPException, Depends
from ..deps import get_session
from sqlalchemy import text
from app.services.hh_events import apply_hh_event

router = APIRouter(prefix="/hh", tags=["hh"])

@router.post("/webhook")
async def hh_webhook(request: Request, session = Depends(get_session)):
    payload = await request.json()
    user_id = session.execute(text("SELECT id FROM users WHERE tg_id=:tg"),
                              {"tg": int(payload["tg_id"])}).scalar_one_or_none()
    if not user_id:
        raise HTTPException(404, "user not found")
    n = apply_hh_event(session,
                       user_id=user_id,
                       resume_uuid=payload["resume_uuid"],
                       vacancy_id=int(payload["vacancy_id"]),
                       event=payload["event"])
    return {"ok": True, "updated": n}
