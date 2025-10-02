from __future__ import annotations

import os
import re
import socket
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[3]
ENV = ROOT / ".env"
if ENV.exists():
    load_dotenv(ENV.as_posix())

def _database_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is empty")

    if url.startswith("DATABASE_URL="):
        url = url.split("=", 1)[1].strip()

    url = re.sub(r"^postgresql\+asyncpg://", "postgresql+psycopg2://", url)

    if "@db" in url and not os.path.exists("/.dockerenv"):
        try:
            socket.getaddrinfo("db", 5432)
        except Exception:
            url = re.sub(r"@db(?::\d+)?", "@localhost:5433", url, count=1)
    return url

DATABASE_URL = _database_url()
engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, class_=Session)

def get_session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
