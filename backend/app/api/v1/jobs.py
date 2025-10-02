from __future__ import annotations

import os
import asyncio
from typing import Any, List, Dict

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/hh/jobs", tags=["hh_jobs"])

HH_API = os.getenv("HH_API_BASE", "https://api.hh.ru").rstrip("/")
USER_AGENT = os.getenv("HH_USER_AGENT", "hhbot/1.0")

# ---------- Models ----------

class Salary(BaseModel):
    minimum: int | None = Field(None, alias="from")
    maximum: int | None = Field(None, alias="to")
    currency: str | None = None

class JobItem(BaseModel):
    id: str
    title: str = Field(alias="name")
    company: str | None = None
    area_id: int | None = None
    salary: Salary | None = None
    published_at: str | None = None
    source: str = "hh"

class JobsResponse(BaseModel):
    items: list[JobItem]
    page: int
    page_size: int
    total: int

class Area(BaseModel):
    id: int
    name: str
    parent_id: int | None = None

# ---------- Helpers ----------

async def _hh_get(path: str, params: Dict[str, Any] | None = None) -> Dict[str, Any] | List[Any]:
    """
    Небольшой ретрай и аккуратные коды ошибок, чтобы фронт не видел 500.
    """
    attempts = 3
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
        for i in range(attempts):
            r = await client.get(f"{HH_API}{path}", params=params)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    raise HTTPException(status_code=502, detail="hh.ru json parse error")
            if r.status_code in (429, 503) and i < attempts - 1:
                retry_after = r.headers.get("Retry-After")
                delay = float(retry_after) if (retry_after or "").isdigit() else (1.5 * (i + 1))
                await asyncio.sleep(delay)
                continue
            if r.status_code == 404:
                raise HTTPException(status_code=404, detail="not found")
            raise HTTPException(status_code=502, detail=f"hh.ru upstream error ({r.status_code})")
    raise HTTPException(status_code=502, detail="hh.ru upstream error")

# ---------- Routes ----------

@router.get("/search", response_model=JobsResponse)
async def search_jobs(
    query: str = Query(""),
    area: int | None = None,
    page: int = Query(0, ge=0),
    page_size: int = Query(20, ge=1, le=100),
    search_field: list[str] | None = Query(None, description="['name','description','company_name']"),
    employment: list[str] | None = Query(None, description="['full','part','project','volunteer','probation']"),
    schedule: list[str] | None = Query(None, description="['fullDay','shift','flexible','remote','flyInFlyOut']"),
    professional_role: list[int] | None = Query(None),
):
    def build_params(include_roles: bool = True) -> dict[str, Any]:
        p: dict[str, Any] = {
            "text": query or "",
            "page": page,
            "per_page": page_size,
        }
        if area is not None:
            p["area"] = area
        if search_field:
            p["search_field"] = search_field
        if employment:
            p["employment"] = employment
        if schedule:
            p["schedule"] = schedule
        if include_roles and professional_role:
            p["professional_role"] = [int(x) for x in professional_role]
        return p

    # 1) Запрос как просили (с ролями, если заданы)
    data = await _hh_get("/vacancies", params=build_params(include_roles=True))
    found = int((data or {}).get("found") or 0)

    # 2) Если ничего не нашли, пробуем без professional_role (fallback)
    if found == 0 and professional_role:
        data = await _hh_get("/vacancies", params=build_params(include_roles=False))
        found = int((data or {}).get("found") or 0)

    raw_items = (data or {}).get("items") or []
    items: list[dict[str, Any]] = []

    for it in raw_items:
        vid = str(it.get("id", "")).strip()

        # аккуратный парсинг
        employer_name = (it.get("employer") or {}).get("name")
        area_obj = (it.get("area") or {})
        try:
            area_id = int(area_obj["id"]) if area_obj.get("id") is not None else None
        except Exception:
            area_id = None

        sal = it.get("salary") or None
        salary_payload = None
        if sal:
            salary_payload = {
                "from": sal.get("from"),
                "to": sal.get("to"),
                "currency": sal.get("currency"),
            }

        items.append(
            {
                "id": vid,
                "name": it.get("name") or "",
                "company": employer_name,
                "area_id": area_id,
                "salary": salary_payload,
                "published_at": it.get("published_at"),
                "source": "hh",
            }
        )

    # Возвращаем строго то, что ожидает фронт
    return {
        "items": items,
        "page": int(data.get("page", page) or page),
        "page_size": page_size,               # берем из запроса
        "total": found,                       # общее число по HH
    }

# строго числовой id, чтобы не конфликтовать с /areas
@router.get("/{vacancy_id:int}", response_model=JobItem)
async def get_job(vacancy_id: int):
    it = await _hh_get(f"/vacancies/{vacancy_id}")  # dict
    area_id = None
    try:
        if it.get("area", {}).get("id") is not None:
            area_id = int(it["area"]["id"])
    except Exception:
        area_id = None

    sal = it.get("salary") or None
    salary_payload = None
    if sal:
        salary_payload = {
            "from": sal.get("from"),
            "to": sal.get("to"),
            "currency": sal.get("currency"),
        }

    return {
        "id": str(it.get("id")),
        "name": it.get("name") or "",
        "company": (it.get("employer") or {}).get("name"),
        "area_id": area_id,
        "salary": salary_payload,
        "published_at": it.get("published_at"),
        "source": "hh",
    }

@router.get("/areas", response_model=list[Area])
async def list_areas():
    data = await _hh_get("/areas")  # list

    def flatten(nodes, parent_id=None):
        out: list[Dict[str, Any]] = []
        for n in nodes:
            nid = int(n["id"])
            out.append({"id": nid, "name": n["name"], "parent_id": int(parent_id) if parent_id is not None else None})
            out.extend(flatten(n.get("areas", []), nid))
        return out

    return flatten(data)
