# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Optional, Tuple

import requests
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy import create_engine

from app.core.config import settings

engine: Engine = create_engine(
    settings.database_url,
    future=True, pool_pre_ping=True, pool_size=3, max_overflow=3, pool_timeout=10,
    connect_args={"connect_timeout": 5},
)

def refresh_access_token(*, user_id: int) -> Tuple[bool, Optional[str]]:
    """
    Обновляет access_token по refresh_token для данного user_id.
    Возвращает (ok, err). ok=True если токен обновлён и записан.
    """
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT t.refresh_token
            FROM hh_tokens t
            WHERE t.user_id = :uid
            ORDER BY t.id DESC LIMIT 1
        """), {"uid": user_id}).first()
        if not row or not row.refresh_token:
            return False, "no refresh_token"

        data = {
            "grant_type": "refresh_token",
            "refresh_token": row.refresh_token,
            "client_id": settings.hh_client_id,
            "client_secret": settings.hh_client_secret,
        }
    try:
        resp = requests.post(f"{settings.hh_oauth_base.rstrip('/')}/oauth/token",
                             data=data, timeout=12)
    except requests.RequestException as e:
        return False, f"hh refresh http error: {e}"

    if resp.status_code != 200:
        return False, f"hh refresh bad status: {resp.status_code} {resp.text}"

    payload = resp.json()
    new_access = payload.get("access_token")
    new_refresh = payload.get("refresh_token") or row.refresh_token
    token_type = payload.get("token_type") or "bearer"
    expires_in = int(payload.get("expires_in") or 3600)
    expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

    with engine.begin() as conn:
        # затираем старую запись — и пишем новую (idempotent)
        conn.execute(text("DELETE FROM hh_tokens WHERE user_id = :uid"), {"uid": user_id})
        conn.execute(text("""
            INSERT INTO hh_tokens(user_id, access_token, refresh_token, token_type, expires_at)
            VALUES (:uid, :at, :rt, :tt, :ea)
        """), {"uid": user_id, "at": new_access, "rt": new_refresh, "tt": token_type, "ea": expires_at})
    return True, None
