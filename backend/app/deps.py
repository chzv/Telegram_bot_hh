# backend/app/deps.py
from .db import SessionLocal

def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
