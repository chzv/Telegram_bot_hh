# backend/app/main.py
from __future__ import annotations

from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
import app.core.compat              # noqa: F401 
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from fastapi import FastAPI
from app.api.router import api_router
from app.api.v1 import payments_cp, cp_webhooks 
import os

app = FastAPI(title="HH Bot API")
app.include_router(payments_cp.router)  
app.include_router(cp_webhooks.router)
app.include_router(api_router)


def _load_env():
    here = Path(__file__).resolve()
    for p in [here] + list(here.parents):
        env = p / ".env"
        if env.exists():
            load_dotenv(env.as_posix())
            break

_load_env()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/healthz", tags=["meta"])
def healthz():
    return {"ok": True}

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = [ROOT / "adminka" / "dist", ROOT / "adminka"]
ADMIN_DIR = next((d for d in CANDIDATES if (d / "index.html").exists()), None)

if ADMIN_DIR:
    app.mount("/admin", StaticFiles(directory=ADMIN_DIR.as_posix(), html=True), name="admin")

    @app.get("/admin/", include_in_schema=False)
    @app.get("/admin/{_:path}", include_in_schema=False)
    def admin_spa(_: str | None = None):
        return FileResponse((ADMIN_DIR / "index.html").as_posix())

import builtins
try:
    from app.core.config import _get as __cfg_get, _get_list as __cfg_get_list
    builtins._get = __cfg_get
    builtins._get_list = __cfg_get_list
except Exception:
    pass

from threading import Thread

def _boot_notifier():
    try:
        from app.services.notifier import start_loop
        start_loop()
    except Exception as e:
        print("[notifier] failed to start:", e)

if os.getenv("ENABLE_NOTIFIER"):
    t = Thread(target=_boot_notifier, daemon=True)
    t.start()
    print("[notifier] started from main.py")

from sqlalchemy import text
from app.db import SessionLocal

def ensure_indexes_once():
    try:
        with SessionLocal() as db:
            db.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_apps_campaign_kind_created
                ON applications(campaign_id, kind, created_at DESC)
            """))
            db.commit()
    except Exception:
        pass

ensure_indexes_once()
