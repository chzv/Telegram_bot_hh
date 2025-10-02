from __future__ import annotations

import os
import json
from typing import Any, Dict, Optional, List

import httpx
import requests

import aiohttp
import logging

import time

# API_BASE уже включает /api/v1
API_BASE = (
    os.getenv("API_BASE_URL")
    or (os.getenv("BACKEND_URL", "http://backend:8000").rstrip("/") + "/api/v1")
).rstrip("/")


def _norm_path(path: str) -> str:
    """
    Превращаем путь в относительный к /api/v1:
    - убираем возможный префикс /api/v1
    - гарантируем ведущий '/'
    """
    p = path if path.startswith("/") else "/" + path
    if p.startswith("/api/v1/"):
        p = p[len("/api/v1"):]  
    return p

# ---------- ASYNC ----------
async def _req(method: str, path: str, **kw):
    url = API_BASE + _norm_path(path)
    timeout = httpx.Timeout(20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.request(method, url, **kw)
        logging.getLogger(__name__).info("API %s %s -> %s", method, url, r.status_code)
        r.raise_for_status()
        ctype = (r.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if ctype == "application/json":
            return r.json()
        try:
            return r.json()
        except Exception:
            return r.text


# ---------- SYNC ----------
def _req_sync(method: str, path: str, **kw):
    url = API_BASE + _norm_path(path)
    timeout = kw.pop("timeout", 20)
    r = requests.request(method, url, timeout=timeout, **kw)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    try:
        return r.json() if ctype == "application/json" else json.loads(r.text)
    except Exception:
        return r.text


def _u(path: str) -> str:
    """Склейка с учётом того, что API_BASE уже содержит /api/v1."""
    p = path if path.startswith("/") else "/" + path
    if p.startswith("/api/v1/"):
        p = p[len("/api/v1"):] 
    return API_BASE.rstrip("/") + p


def _ok_json_or_text(r: requests.Response):
    if r.ok:
        try:
            return r.json()
        except Exception:
            return {"ok": True}
    try:
        detail = r.json()
    except Exception:
        detail = r.text
    raise RuntimeError(f"{r.status_code} {r.reason}: {detail}")


# ---------- HH auth ----------
async def get_hh_auth_url(tg_id: int):
    return await _req("GET", "/hh/login", params={"tg_id": tg_id})


async def get_link_status(tg_id: int):
    return await _req("GET", "/hh/link-status", params={"tg_id": tg_id})


# ---------- Users ----------
async def users_seen(tg_id: int, username: str | None = None) -> None:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.post(
                f"{API_BASE}/users/seen",
                json={"tg_id": int(tg_id), "username": username},
            )
    except Exception:
        pass

async def register_user(tg_id: int, username: Optional[str]) -> None:
    await _req("POST", "/users/register", params={"tg_id": tg_id, "username": username or ""})

async def user_stats(tg_id: int) -> Dict[str, Any]:
    return await _req("GET", "/users/stats", params={"tg_id": tg_id})

async def stats_resumes(tg_id: int) -> dict:
    return await _req("GET", "/stats/resumes", params={"tg_id": tg_id, "_ts": int(time.time())})

async def stats_resume(tg_id: int, resume_id: int) -> dict:
    return await _req("GET", f"/stats/resumes/{resume_id}", params={"tg_id": tg_id, "_ts": int(time.time())})

# ---------- HH jobs ----------
async def hh_areas() -> List[dict]:
    return await _req("GET", "/hh/jobs/areas")


async def hh_search(
    query: str,
    area: Optional[int],
    page: int = 0,
    page_size: int = 20,
    *,
    schedules: Optional[List[str]] = None,
    employment: Optional[List[str]] = None,
    professional_roles: Optional[List[int]] = None,
    search_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "query": query or "",
        "text": query or "",
        "page": int(page),
        "page_size": int(page_size),
    }
    if area is not None:
        try:
            params["area"] = int(area)
        except Exception:
            pass
    if schedules:
        params["schedule"] = list(schedules)
    if employment:
        params["employment"] = list(employment)
    if professional_roles:
        params["professional_role"] = [int(x) for x in professional_roles]
    if search_fields:
        params["search_field"] = list(search_fields)

    return await _req("GET", "/hh/jobs/search", params=params)


# ---------- Queue / dispatch ----------
async def queue_applications(
    tg_id: int,
    vacancy_ids: list[int],
    cover_letter: str | None,
    kind: str,
    resume_id: str,
):
    payload = {
        "tg_id": tg_id,
        "vacancies": vacancy_ids,
        "resume_id": resume_id,
        "cover_letter": cover_letter,
        "kind": kind,
    }
    return await _req("POST", "/hh/applications/queue", json=payload)


async def dispatch_now(limit: int = 50, dry_run: bool = False) -> Dict[str, Any]:
    return await _req(
        "POST", "/hh/applications/dispatch",
        params={"limit": int(limit), "dry_run": bool(dry_run)}
    )

# ---------- Payments / Subs / Referrals ----------
async def payments_status(tg_id: int) -> Dict[str, Any]:
    return await _req("GET", "/payments/status", params={"tg_id": tg_id})

async def payments_invoice(tg_id: int, tariff_id: int, capacity: int = 1) -> dict:
    return await _req(
        "POST",
        "/payments/invoice",
        params={"tg_id": tg_id},
        json={"tariff_id": int(tariff_id), "capacity": int(capacity)},
    )

async def subscription_current(tg_id: int) -> Dict[str, Any]:
    return await _req("GET", "/subscriptions/current", params={"tg_id": tg_id})

async def referrals_me(tg_id: int) -> Dict[str, Any]:
    return await _req("GET", "/referrals/me", params={"tg_id": tg_id})

async def referrals_generate(tg_id: int) -> Dict[str, Any]:
    return await _req("POST", "/referrals/generate", params={"tg_id": tg_id})

async def referrals_track(tg_id: int, code: str) -> Dict[str, Any]:
    return await _req("POST", "/referrals/track", params={"tg_id": tg_id, "code": code})


# ---------- Resumes & Auto (SYNC wrappers, дергаем через to_thread) ----------

def hh_resumes(tg_id: int) -> list[dict]:
    """Вернёт нормализованный список резюме текущего пользователя."""
    r = requests.get(_u("/hh/resumes"), params={"tg_id": tg_id}, timeout=10)
    r.raise_for_status()
    raw = r.json()

    data = []
    if isinstance(raw, dict):
        data = raw.get("items") or raw.get("resumes") or raw.get("data") or []
    elif isinstance(raw, list):
        data = raw

    out = []
    for it in data:
        if not isinstance(it, dict):
            continue
        if it.get("visible") is False:
            continue
        _id = it.get("id") or it.get("resume_id") or it.get("uuid") or it.get("resumeUUID")
        if not _id:
            continue
        out.append(
            {
                "id": str(_id),
                "title": (it.get("title") or it.get("name") or "Резюме").strip(),
                "updated_at": it.get("updated_at") or it.get("updatedAt"),
            }
        )
    out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return out


def hh_resumes_sync(tg_id: int):
    """Принудительная синхронизация резюме на бэке (пробуем и query, и json)."""
    r = requests.post(_u("/hh/resumes/sync"), params={"tg_id": tg_id}, timeout=15)
    if not r.ok:
        r = requests.post(_u("/hh/resumes/sync"), json={"tg_id": tg_id}, timeout=15)
    return _ok_json_or_text(r)


def link_status(tg_id: int):
    r = requests.get(_u("/hh/link-status"), params={"tg_id": tg_id}, timeout=10)
    r.raise_for_status()
    return r.json()


def authorize_url(tg_id: int):
    r = requests.get(_u("/hh/authorize-url"), params={"tg_id": tg_id}, timeout=10)
    r.raise_for_status()
    return r.json()

# ---------- Saved Requests (SYNC) ----------

async def saved_requests_create(tg_id: int, payload: dict) -> dict:
    rid = payload.get("resume_id") or payload.get("resume")
    if rid and not payload.get("resume"):
        payload["resume"] = rid

    return await _req(
        "POST",
        "/saved-requests",
        params={"tg_id": int(tg_id)},
        json=payload,
    )

def saved_requests_create_sync(tg_id: int, payload: dict) -> dict:
    """
    Создать сохранённый запрос.
    Бэкенд ожидает tg_id в QUERY (?tg_id=...), а тело — JSON с полями запроса.
    """
    r = requests.post(
        _u("/saved-requests"),
        params={"tg_id": int(tg_id)},   
        json=payload,                   
        timeout=20,
    )
    return _ok_json_or_text(r)

# ---------- Saved Requests (SYNC) ----------
def saved_requests_list_sync(tg_id: int) -> list[dict]:
    r = requests.get(_u("/saved-requests"), params={"tg_id": int(tg_id)}, timeout=20)
    res = _ok_json_or_text(r)
    if isinstance(res, dict) and "items" in res and isinstance(res["items"], list):
        return res["items"]
    return res if isinstance(res, list) else []

def saved_requests_delete_sync(tg_id: int, req_id: int) -> dict:
    r = requests.delete(_u(f"/saved-requests/{int(req_id)}"), params={"tg_id": tg_id}, timeout=10)
    return _ok_json_or_text(r)

# ---------- Saved Requests (ASYNC wrappers) ----------
import asyncio

async def saved_requests_list(tg_id: int) -> list[dict]:
    return await asyncio.to_thread(saved_requests_list_sync, tg_id)

async def saved_requests_delete(tg_id: int, req_id: int) -> dict:
    return await asyncio.to_thread(saved_requests_delete_sync, tg_id, req_id)

def auto_upsert(
    tg_id: int,
    name: str,
    resume_id: str,
    query_params: str,
    daily_limit: int = 5,
    run_at: str | None = None,
    cover_letter: str = "",
    active: bool = True,
) -> dict:
    """
    Надёжное сохранение авто-правила.
    Бэкенд требует: tg_id, name, resume_id, query_params (+ опции).
    """
    url = _u("/hh/auto/upsert")
    title = (name or "Поиск").strip()

    base = {
        "tg_id": int(tg_id),
        "name": title,
        "resume_id": str(resume_id),
        "daily_limit": int(daily_limit or 1),
        "cover_letter": cover_letter or "",
        "active": bool(active),
    }
    if run_at and str(run_at).strip():
        base["run_at"] = str(run_at).strip()

    body = dict(base, query_params=str(query_params or ""))

    r = requests.post(url, json=body, timeout=20)
    return _ok_json_or_text(r)


def auto_plan() -> dict:
    """Планирует заявки kind='auto' на сегодня."""
    r = requests.post(_u("/hh/auto/plan"), timeout=15)
    return _ok_json_or_text(r)

async def quota_current(tg_id: int) -> dict:
    """
    Возвращает сырую квоту с бэка.
    Пример: {tg_id, user_id, tariff, limit, hard_cap, used, remaining, reset_time_msk}
    """
    q = await _req("GET", "/quota", params={"tg_id": int(tg_id)})
    # алиас, если вдруг сервер вернул tariff_limit
    if isinstance(q, dict) and "limit" not in q and "tariff_limit" in q:
        q["limit"] = q.get("tariff_limit")
    return q
# ---------- Cover letters (SYNC) ----------
def cover_letters_list_sync(tg_id: int) -> list[dict]:
    r = requests.get(_u("/cover-letters"), params={"tg_id": tg_id}, timeout=10)
    r.raise_for_status()
    raw = r.json()
    return raw if isinstance(raw, list) else []


def cover_letters_create_sync(tg_id: int, title: str, body: str) -> dict:
    payload = {
        "tg_id": int(tg_id),
        "title": str(title or "").strip(),
        "body": str(body or "").strip(),
    }
    r = requests.post(_u("/cover-letters"), json=payload, timeout=15)
    return _ok_json_or_text(r)


def cover_letters_update_sync(
    tg_id: int, letter_id: int, *, title: str | None = None, body: str | None = None
) -> dict:
    payload = {"tg_id": int(tg_id), "title": title, "body": body}
    r = requests.put(_u(f"/cover-letters/{int(letter_id)}"), json=payload, timeout=15)
    return _ok_json_or_text(r)


def cover_letters_delete_sync(tg_id: int, letter_id: int) -> dict:
    r = requests.delete(_u(f"/cover-letters/{int(letter_id)}"), params={"tg_id": tg_id}, timeout=10)
    return _ok_json_or_text(r)


# ---------- Cover letters (ASYNC wrappers) ----------
import asyncio

async def cover_letters_list(tg_id: int) -> list[dict]:
    return await asyncio.to_thread(cover_letters_list_sync, tg_id)

async def cover_letters_create(tg_id: int, title: str, body: str) -> dict:
    return await asyncio.to_thread(cover_letters_create_sync, tg_id, title, body)

async def cover_letters_update(
    tg_id: int, letter_id: int, *, title: str | None = None, body: str | None = None
) -> dict:
    return await asyncio.to_thread(cover_letters_update_sync, tg_id, letter_id, title=title, body=body)

async def cover_letters_delete(tg_id: int, letter_id: int) -> dict:
    return await asyncio.to_thread(cover_letters_delete_sync, tg_id, letter_id)
    
    
def auto_status_sync(tg_id: int) -> dict:
    r = requests.get(_u("/hh/auto/status"), params={"tg_id": tg_id}, timeout=10)
    return _ok_json_or_text(r)

def auto_set_active_sync(tg_id: int, active: bool) -> dict:
    r = requests.post(_u("/hh/auto/active"), json={"tg_id": int(tg_id), "active": bool(active)}, timeout=10)
    return _ok_json_or_text(r)

# --- Campaigns ---------------------------------------------------------------
async def campaigns_list(tg_id: int, page: int = 1, page_size: int = 20) -> dict:
    return await _req("GET", "/hh/campaigns", params={"tg_id": int(tg_id), "page": page, "page_size": page_size})

async def campaign_upsert(
    tg_id: int,
    title: str,
    saved_request_id: int | None,
    resume_id: str,
    daily_limit: int = 200,
    *,
    query: str | None = None,
    area: int | None = None,
    work_format: list[str] | None = None,
    employment: list[str] | None = None,
    professional_roles: list[int] | None = None,
    search_fields: list[str] | None = None,
) -> dict:
    payload = {
        "tg_id": int(tg_id),
        "title": title,
        "saved_request_id": saved_request_id,
        "resume_id": resume_id,
        "daily_limit": int(daily_limit),
    }
    if query:                   payload["query"] = query
    if area is not None:        payload["area"] = area
    if work_format:             payload["work_format"] = list(work_format)
    if employment:              payload["employment"] = list(employment)
    if professional_roles:      payload["professional_roles"] = [int(x) for x in professional_roles]
    if search_fields:           payload["search_fields"] = list(search_fields)

    return await _req("POST", "/hh/campaigns/upsert", json=payload)
    
async def campaign_start(tg_id: int, campaign_id: int) -> dict:
    return await _req("POST", "/hh/campaigns/start", json={"tg_id": int(tg_id), "id": int(campaign_id)})

async def campaign_stop(tg_id: int, campaign_id: int) -> dict:
    return await _req("POST", "/hh/campaigns/stop", json={"tg_id": int(tg_id), "id": int(campaign_id)})
    
async def campaign_delete(tg_id: int, campaign_id: int) -> dict:
    return await _req(
        "POST",
        "/hh/campaigns/delete",
        json={"tg_id": int(tg_id), "id": int(campaign_id)},
    )

async def campaign_send_now(tg_id: int, campaign_id: int, *, limit: int | None = None):
    payload = {"tg_id": int(tg_id), "id": int(campaign_id)}
    if limit is not None:
        payload["limit"] = int(limit)
    return await _req("POST", "/hh/campaigns/send_now", json=payload)