# app/services/hh_events.py 
from sqlalchemy import text
EVENT_MAP = {
    "viewed": "viewed", "resume_viewed": "viewed", "read": "viewed", "seen": "viewed",
    "invite": "invited", "invited": "invited", "offer": "invited",
    "rejected": "declined", "declined": "declined", "denied": "declined", "failed": "declined",
}

def apply_hh_event(session, *, user_id:int, resume_uuid:str, vacancy_id:int, event:str):
    ev = EVENT_MAP.get(event.lower().strip())
    if not ev:
        return 0
    app_id = session.execute(text("""
        SELECT id FROM applications
        WHERE user_id=:u AND vacancy_id=:v AND resume_id=:r
    """), {"u": user_id, "v": vacancy_id, "r": resume_uuid}).scalar_one_or_none()
    if not app_id:
        return 0
    # если целевой статус — обновляем саму заявку
    if ev in ("invited","declined"):
        session.execute(text("""
            UPDATE applications SET status=:st, updated_at=now()
            WHERE id=:id
        """), {"st": ev, "id": app_id})
    # обязательно логируем событие
    session.execute(text("""
        INSERT INTO applications_log (application_id, event) VALUES (:id, :ev)
    """), {"id": app_id, "ev": ev})
    return 1
