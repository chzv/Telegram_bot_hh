# backend/app/api/v1/stats.py
from fastapi import APIRouter, Query, HTTPException, Depends
from sqlalchemy import text, bindparam
from ..deps import get_session

router = APIRouter(prefix="/stats", tags=["stats"])

def _user_id_by_tg(session, tg_id: int):
    return session.execute(text("SELECT id FROM users WHERE tg_id=:tg"), {"tg": tg_id}).scalar_one_or_none()

@router.get("/resumes")
def list_resumes(tg_id: int = Query(..., ge=1), session = Depends(get_session)):
    uid = _user_id_by_tg(session, tg_id)
    if uid is None:
        return {"items": []}
    rows = session.execute(text("""
        SELECT id, COALESCE(NULLIF(title,''),'Резюме') AS title
        FROM resumes
        WHERE user_id=:uid
        ORDER BY id DESC
        LIMIT 100
    """), {"uid": uid}).mappings().all()
    return {"items": [{"id": r["id"], "name": r["title"]} for r in rows]}

@router.get("/resumes/{resume_id}")
def resume_stats(resume_id: int, tg_id: int = Query(..., ge=1), session = Depends(get_session)):
    uid = _user_id_by_tg(session, tg_id)
    if uid is None:
        raise HTTPException(404, "user not found")

    row = session.execute(
        text("""
            SELECT COALESCE(NULLIF(title,''),'Резюме') AS title,
                   resume_id::text                    AS resume_uuid
            FROM resumes
            WHERE id=:rid AND user_id=:uid
        """),
        {"rid": resume_id, "uid": uid}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "resume not found")

    title     = row["title"]
    rid_text  = row["resume_uuid"]   
    rid_int_s = str(resume_id)       

    base = session.execute(text("""
        WITH src AS (
          SELECT created_at
          FROM applications
          WHERE user_id=:uid
            AND (resume_id=:rid_text OR resume_id=:rid_int_s)
        )
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE created_at >= date_trunc('day', now())) AS today
        FROM src
    """), {"uid": uid, "rid_text": rid_text, "rid_int_s": rid_int_s}).mappings().first() or {}
    total = int(base.get("total") or 0)
    today = int(base.get("today") or 0)

    invited = rejected = 0
    has_log = session.execute(text("SELECT to_regclass('public.applications_log')")).scalar() == 'applications_log'
    
    if has_log:
        status_col = session.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name='applications_log'
              AND column_name IN ('event','status','new_status','to_status')
            ORDER BY CASE column_name
                     WHEN 'event' THEN 0
                     WHEN 'status' THEN 1
                     WHEN 'new_status' THEN 2
                     WHEN 'to_status' THEN 3
                     ELSE 100 END
            LIMIT 1
        """)).scalar()
    
        if status_col:
            invite_like  = ["invite", "invited", "interview", "offer"]
            decline_like = ["decline", "declined", "rejected", "auto_rejected", "reject", "denied", "failed"]
        
            sql = f"""
                WITH src AS (
                  SELECT lower(al.{status_col}::text) AS st
                  FROM applications a
                  JOIN applications_log al ON al.application_id = a.id
                  WHERE a.user_id = :uid
                    AND (a.resume_id = :rid_text OR a.resume_id = :rid_int_s)
                )
                SELECT
                  COUNT(*) FILTER (WHERE src.st IN :inv) AS invited,
                  COUNT(*) FILTER (WHERE src.st IN :dec) AS rejected
                FROM src
            """
        
            stmt = (
                text(sql)
                .bindparams(
                    bindparam("inv", value=tuple(invite_like), expanding=True),
                    bindparam("dec", value=tuple(decline_like), expanding=True),
                )
            )
            row2 = session.execute(
                stmt,
                {"uid": uid, "rid_text": rid_text, "rid_int_s": str(resume_id)},
            ).mappings().first() or {}
            invited  = int(row2.get("invited") or 0)
            rejected = int(row2.get("rejected") or 0)

    conv = round((100.0 * invited / total), 1) if total else 0.0
    return {
        "name": title,
        "total_responses": total,
        "responses_today": today,
        "invites": invited,
        "declines": rejected,
        "conversion": conv,
    }

