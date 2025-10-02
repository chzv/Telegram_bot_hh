# backend/app/api/v1/auto_scheduler.py
from fastapi import APIRouter, HTTPException
from app.services.auto_scheduler import dispatch_auto_once 

router = APIRouter(prefix="/hh/auto", tags=["auto"])

@router.post("/plan")
async def plan():
    try:
        stats = await dispatch_auto_once()
        return {"queued": int(stats.get("queued", 0)), **stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
