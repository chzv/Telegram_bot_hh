from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import create_engine, text
from app.core.config import settings
import csv
from io import StringIO

router = APIRouter(prefix="/admin/export", tags=["admin"])

engine = create_engine(settings.database_url, future=True, pool_pre_ping=True)

def _iter_csv(rows):
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["id","user_id","vacancy_id","status","attempt_count","created_at","sent_at","error"])
    yield buf.getvalue(); buf.seek(0); buf.truncate(0)
    for r in rows:
        w.writerow([r.id, r.user_id, r.vacancy_id, r.status, r.attempt_count, r.created_at, r.sent_at, r.error])
        yield buf.getvalue(); buf.seek(0); buf.truncate(0)

@router.get("/applications.csv")
def export_applications(status: str | None = Query(None), limit: int = Query(10000, ge=1, le=200000)):
    q = """
      SELECT id, user_id, vacancy_id, status, attempt_count, created_at, sent_at, error
      FROM applications
    """
    params = {}
    if status:
        q += " WHERE status = :st"
        params["st"] = status
    q += " ORDER BY id DESC LIMIT :lim"
    params["lim"] = limit
    with engine.begin() as conn:
        rows = conn.execute(text(q), params).mappings()
        return StreamingResponse(_iter_csv(rows),
                                 media_type="text/csv",
                                 headers={"Content-Disposition":"attachment; filename=applications.csv"})
