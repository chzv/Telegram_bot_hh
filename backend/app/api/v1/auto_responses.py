# backend/app/api/v1/auto_responses.py
from __future__ import annotations

from urllib.parse import urlparse, parse_qsl, urlencode
from typing import Optional, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from app.core.config import settings

router = APIRouter()

ALLOW_KEYS = {
    "text", "area", "professional_role", "specialization",
    "experience", "employment", "schedule", "work_format",
    "only_with_salary", "salary", "currency",
    "search_field", "label", "order_by" 
}
DROP_VALUES = {"", None}

def _conn():
    eng = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    return eng.connect()

def _normalize_query(qs_or_url: str) -> str:
    """
    На вход: полный урл из HH или чистый querystring.
    На выход: канонический querystring (отсортированный, без page/per_page и пустых значений).
    """
    raw_qs = qs_or_url
    if "?" in qs_or_url:
        raw_qs = urlparse(qs_or_url).query

    pairs = parse_qsl(raw_qs, keep_blank_values=True)
    filt: List[tuple[str, str]] = []
    for k, v in pairs:
        if k in {"page", "per_page"}:
            continue
        if k not in ALLOW_KEYS:
            continue
        if v in DROP_VALUES:
            continue
        filt.append((k, v))

    filt.sort(key=lambda kv: (kv[0], kv[1]))
    return urlencode(filt, doseq=True)

class AutoUpsertIn(BaseModel):
    tg_id: int = Field(..., ge=1)
    name: str = Field(..., min_length=1)
    resume_id: str = Field(..., min_length=1)
    search_url: Optional[str] = None
    query_params: Optional[str] = None
    daily_limit: int = Field(50, ge=1, le=500)
    run_at: str = Field("09:00", regex=r"^\d{2}:\d{2}$")
    active: bool = True
