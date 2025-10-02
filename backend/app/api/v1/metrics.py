from fastapi import APIRouter
import os, socket, re
import psycopg2

router = APIRouter(prefix="/metrics", tags=["metrics"])

def _dsn_pg() -> str:
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        try:
            from app.core.config import settings
            dsn = (getattr(settings, "database_url", "") or "").strip()
        except Exception:
            pass
    if not dsn:
        raise RuntimeError("No DATABASE_URL found")

    if dsn.startswith("postgresql+psycopg2://"):
        dsn = dsn.replace("postgresql+psycopg2://", "postgresql://", 1)
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

    if not os.path.exists("/.dockerenv"):
        try:
            socket.getaddrinfo("db", 5432)
        except Exception:
            host = "localhost"
            port = os.getenv("PGPORT_HOST", "5433")
            dsn = re.sub(r"@db(?::\d+)?", f"@{host}:{port}", dsn, count=1)
    return dsn

@router.get("/summary")
def metrics_summary():
    try:
        dsn = _dsn_pg()
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM users")
                users_total = int(cur.fetchone()[0] or 0)
                cur.execute("SELECT count(*) FROM applications")
                appl_total  = int(cur.fetchone()[0] or 0)
        return {"ok": True, "users_total": users_total, "applications_total": appl_total}
    except Exception as e:
        return {"ok": False, "users_total": 0, "applications_total": 0, "note": "fallback", "error": str(e)}
