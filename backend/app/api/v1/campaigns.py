# app/api/v1/campaigns.py
from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, Query, Body, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from app.db import SessionLocal
from urllib.parse import parse_qsl, urlencode
from typing import Optional
import httpx
from app.services.limits import quota_for_user

router = APIRouter(prefix="/hh", tags=["campaigns"])

# ---------- helpers ----------
SCHEDULE_MAP = {
    "REMOTE": "remote", "remote": "remote",
    "FULLDAY": "fullDay", "fullDay": "fullDay",
    "SHIFT": "shift", "shift": "shift",
    "FLEXIBLE": "flexible", "flexible": "flexible",
    "ROTATIONAL": "flyInFlyOut", "flyInFlyOut": "flyInFlyOut",
}

FIRST_BATCH_DEFAULT = 150

def _resolve_user_id(db, tg_id: int | None, user_id: int | None) -> int:
    """Разрешаем пользователя: предпочитаем внутренний user_id, иначе ищем по tg_id."""
    if user_id:
        return int(user_id)
    if tg_id:
        uid = db.execute(
            text("SELECT id FROM users WHERE tg_id=:t LIMIT 1"),
            {"t": tg_id},
        ).scalar()
        if not uid:
            raise HTTPException(404, "user not found")
        return int(uid)
    raise HTTPException(400, "tg_id or user_id is required")

def _require(val, msg: str):
    if val is None or (isinstance(val, str) and not val.strip()):
        raise HTTPException(400, msg)
    return val

def _get_hh_access_token(db, user_id: int) -> Optional[str]:
    return db.execute(text("""
        SELECT access_token
          FROM hh_tokens
         WHERE user_id = :u
           AND access_token IS NOT NULL
           AND access_token <> ''
         ORDER BY updated_at DESC, id DESC
         LIMIT 1
    """), {"u": user_id}).scalar()

def _normalize_qs_for_hh(qp: str) -> list[tuple[str, str]]:
    allowed = {
        "text","area","professional_role","employment","search_field","schedule",
        "only_with_salary","order_by"
    }
    pairs = parse_qsl(qp, keep_blank_values=False)
    norm: list[tuple[str,str]] = []
    for k, v in pairs:
        if k not in allowed:
            continue
        if k == "schedule":
            v = SCHEDULE_MAP.get(v, v).strip()
        elif k == "employment":
            v = (v or "").lower().strip()
            if v not in {"full","part","project","probation","volunteer"}:
                continue
        elif k == "professional_role":
            # только цифры
            if not str(v).isdigit():
                continue
        elif k == "search_field":
            # допустимые у HH: name | company_name | description
            if v not in {"name","company_name","description"}:
                continue
        norm.append((k, v))
    return norm
    
def _hh_search_by_qs(db, user_id: int, qp: str, limit: int) -> list[dict]:
    token = _get_hh_access_token(db, user_id)  # может быть None — это ок
    headers = {
        "User-Agent": "offerbot/1.0",
        "Accept": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    base = "https://api.hh.ru/vacancies"
    params_base = _normalize_qs_for_hh(qp)
    per_page = min(max(1, limit), 100)

    def _fetch(params: list[tuple[str,str]]) -> tuple[list[dict], Optional[dict]]:
        out: list[dict] = []
        err_json: Optional[dict] = None
        with httpx.Client(timeout=12.0, headers=headers) as client:
            page = 0
            dropped_auth = False
            while len(out) < limit and page < 20:
                q = params + [("per_page", str(per_page)), ("page", str(page))]
                try:
                    r = client.get(base, params=q)
                except httpx.HTTPError:
                    break 
    
                if r.status_code == 401:
                    # токен протух — пробуем без авторизации один раз
                    if "Authorization" in client.headers and not dropped_auth:
                        client.headers.pop("Authorization", None)
                        dropped_auth = True
                        continue
                    break
    
                if r.status_code == 400:
                    # запомнили ошибку и попробуем упростить запрос выше
                    try:
                        err_json = r.json()
                    except Exception:
                        err_json = {"errors": [{"type": "unknown_400"}]}
                    break
    
                if r.status_code in (403, 429, 500, 502, 503, 504):
                    # временные/доступ — попробуем без авторизации один раз, потом выходим
                    if "Authorization" in client.headers and not dropped_auth:
                        client.headers.pop("Authorization", None)
                        dropped_auth = True
                        continue
                    break
    
                r.raise_for_status()
    
                items = r.json().get("items", [])
                if not items:
                    break
                for it in items:
                    vid = str(it.get("id") or "").strip()
                    if vid:
                        out.append({"id": vid})
                        if len(out) >= limit:
                            break
                page += 1
    
        return out[:limit], err_json

    # Попытка 1 — как есть
    items, err = _fetch(params_base)
    if items:
        return items
    
    # Попытка 2 — убираем professional_role (частая причина 400)
    if any(k == "professional_role" for k, _ in params_base):
        params2 = [(k, v) for (k, v) in params_base if k != "professional_role"]
        items, err = _fetch(params2)
        if items:
            return items

    # Попытка 3 — убираем search_field (редко, но бывает)
    if any(k == "search_field" for k, _ in params_base):
        params3 = [(k, v) for (k, v) in params_base if k != "search_field"]
        items, err = _fetch(params3)
        if items:
            return items

    # Попытка 4 — только text + area
    text_val = next((v for k, v in params_base if k == "text"), None)
    area_vals = [v for k, v in params_base if k == "area"]
    params4: list[tuple[str,str]] = []
    if text_val:
        params4.append(("text", text_val))
    if area_vals:
        params4.append(("area", area_vals[0]))
    items, _ = _fetch(params4)
    return items

# ---------- models ----------
class CampaignUpsert(BaseModel):
    tg_id: int | None = None
    user_id: int | None = None
    title: str
    saved_request_id: int | None = None
    resume_id: str
    daily_limit: int = 200

class CampaignId(BaseModel):
    id: int
    tg_id: int | None = None
    user_id: int | None = None

# ---------- endpoints ----------
def _from_qp(qp: str) -> dict:
    """
    Разобрать query_params из saved_requests и вернуть удобные поля:
    - search_url (без resume параметра, чтобы ссылка была «просмотровая»)
    - work_format, employment, professional_roles, search_fields, text, area, resume
    """
    if not qp:
        return {"search_url": None}

    pairs = parse_qsl(qp, keep_blank_values=False)
    bag: dict[str, list[str]] = {}
    for k, v in pairs:
        bag.setdefault(k, []).append(v)

    def one(key: str):
        return (bag.get(key) or [None])[0]

    work_format        = bag.get("work_format") or bag.get("schedule") or []
    employment         = bag.get("employment") or []
    professional_roles = bag.get("professional_role") or []
    search_fields      = bag.get("search_field") or []

    text = one("text")
    areas  = bag.get("area") or [] 
    resume = one("resume")

    qp_no_resume = [(k, v) for (k, vs) in bag.items() for v in vs if k != "resume"]
    search_url = "https://hh.ru/search/vacancy?" + urlencode(qp_no_resume, doseq=True)

    return {
        "text": text,
        "areas": areas,
        "resume": resume,
        "work_format": work_format,
        "employment": employment,
        "professional_roles": professional_roles,
        "search_fields": search_fields,
        "search_url": search_url,
    }

@router.get("/campaigns")
def list_campaigns(
    tg_id: int | None = Query(None),
    user_id: int | None = Query(None),
    page: int = 1,
    page_size: int = 20,
):
    off = (page - 1) * page_size
    with SessionLocal() as db:
        uid = _resolve_user_id(db, tg_id, user_id)
        
        total = db.execute(
            text("SELECT COUNT(*) FROM campaigns WHERE user_id=:uid"),
            {"uid": uid},
        ).scalar()
        
        rows = db.execute(
            text("""
                SELECT
                  c.id, c.user_id, c.title, c.status,
                  c.created_at, c.updated_at, c.started_at, c.stopped_at,
                  c.resume_id, c.saved_request_id,
                  sr.query_params, sr.query, sr.area, sr.employment,
                  sr.professional_roles, sr.search_fields, sr.cover_letter,
                  (
                    SELECT r.title
                    FROM resumes r
                    WHERE r.user_id = c.user_id
                      AND r.resume_id = c.resume_id
                    LIMIT 1
                  ) AS resume_title,
                  s.sent_count,
                  s.sent_today,
                  s.last_sent_at
                FROM campaigns c
                LEFT JOIN saved_requests sr ON sr.id = c.saved_request_id
                LEFT JOIN LATERAL (
                  SELECT
                    COUNT(*) FILTER (WHERE a.status='sent')::int AS sent_count,
                    COUNT(*) FILTER (WHERE a.status='sent' AND a.created_at::date = now()::date)::int AS sent_today,
                    COUNT(*) FILTER (WHERE a.status IN ('queued','retry'))::int AS pending_apps,
                    COUNT(*) FILTER (WHERE a.status IN ('queued','retry') AND a.created_at::date = now()::date)::int AS pending_apps_today,
                    COALESCE((SELECT COUNT(*) FROM applications_queue aq WHERE aq.campaign_id = c.id), 0)::int AS pending_queue,
                    COALESCE((SELECT COUNT(*) FROM applications_queue aq WHERE aq.campaign_id = c.id AND aq.created_at::date = now()::date), 0)::int AS pending_queue_today,
                    MAX(a.sent_at) AS last_sent_at
                  FROM applications a
                  WHERE a.campaign_id = c.id
                ) s ON TRUE
                WHERE c.user_id = :uid
                ORDER BY c.id DESC
                LIMIT :lim OFFSET :off
            """),
            {"uid": uid, "lim": page_size, "off": off},
        ).mappings().all()
        items = []
        for r in rows:
            d = dict(r)
            parsed = _from_qp((d.get("query_params") or "").strip())
            # визуальная ссылка
            d["search_url"] = parsed.get("search_url")
            # массивы и текстовые поля для карточки
            d["work_format"]        = parsed.get("work_format")        or []
            d["employment"]         = parsed.get("employment")         or (d.get("employment") or [])
            d["professional_roles"] = parsed.get("professional_roles") or (d.get("professional_roles") or [])
            d["search_fields"]      = parsed.get("search_fields")      or (d.get("search_fields") or [])
            # география (массив)
            areas_from_qp = parsed.get("areas") or []
            d["areas"] = areas_from_qp or ([str(d["area"])] if d.get("area") else [])
            d["query"]              = d.get("query") or parsed.get("text") or ""
            d["cover_letter"] = (d.get("cover_letter") or "")
            d["sent_count"]   = int(d.get("sent_count") or 0)
            d["sent_today"]   = int(d.get("sent_today") or 0)
            d["queued_count"] = int((d.get("pending_apps") or 0) + (d.get("pending_queue") or 0))
            d["queued_today"] = int((d.get("pending_apps_today") or 0) + (d.get("pending_queue_today") or 0))
            items.append(d)
        return {"items": items, "total": int(total or 0), "page": page, "page_size": page_size}

@router.post("/campaigns/upsert")
def upsert_campaign(p: CampaignUpsert):
    now = datetime.utcnow()
    with SessionLocal() as db:
        uid = _resolve_user_id(db, p.tg_id, p.user_id)
        _require(p.resume_id, "resume_id is required")
        _require(p.saved_request_id, "saved_request_id is required")

        new_id = db.execute(
            text("""
                INSERT INTO campaigns (
                    user_id, title, saved_request_id, resume_id,
                    daily_limit, status, created_at, updated_at
                )
                VALUES (:uid, :title, :srid, :rid, :lim, 'stopped', :now, :now)
                ON CONFLICT (user_id, resume_id, saved_request_id) DO UPDATE
                SET title = EXCLUDED.title,
                    daily_limit = EXCLUDED.daily_limit,
                    updated_at = :now
                RETURNING id
            """),
            {
                "uid": uid,
                "title": p.title,
                "srid": p.saved_request_id,
                "rid": p.resume_id,
                "lim": p.daily_limit,
                "now": now,
            },
        ).scalar_one()
        db.commit()
        return {"id": int(new_id)}

@router.post("/campaigns/start")
def start_campaign(p: CampaignId):
    with SessionLocal() as db:
        uid = _resolve_user_id(db, p.tg_id, p.user_id)

        # запрет второй активной
        exists = db.execute(
            text("""
                SELECT 1
                  FROM campaigns
                 WHERE user_id=:uid AND status='active' AND id<>:cid
            """),
            {"uid": uid, "cid": p.id},
        ).scalar()
        if exists:
            raise HTTPException(status_code=409, detail="another active campaign exists")

        # проверка привязки HH (жёстко не пускаем неподвязанных)
        tok = db.execute(
            text("SELECT 1 FROM hh_tokens WHERE user_id=:u LIMIT 1"),
            {"u": uid},
        ).scalar()
        if not tok:
            raise HTTPException(status_code=400, detail="hh account is not linked")

        updated = db.execute(
            text("""
                UPDATE campaigns
                   SET status='active',
                       started_at = COALESCE(started_at, now()),
                       updated_at = now()
                 WHERE id=:cid AND user_id=:uid
                 RETURNING id
            """),
            {"cid": p.id, "uid": uid},
        ).fetchone()
        if not updated:
            raise HTTPException(status_code=404, detail="campaign not found")
        db.commit()
    return {"ok": True}

@router.post("/campaigns/stop")
def stop_campaign(p: CampaignId):
    with SessionLocal() as db:
        uid = _resolve_user_id(db, p.tg_id, p.user_id)
        updated = db.execute(
            text("""
                UPDATE campaigns
                   SET status='stopped',
                       stopped_at = now(),
                       updated_at = now()
                 WHERE id=:cid AND user_id=:uid
                 RETURNING id
            """),
            {"cid": p.id, "uid": uid},
        ).fetchone()
        if not updated:
            raise HTTPException(status_code=404, detail="campaign not found")
        db.commit()
    return {"ok": True}

class CampaignDeleteRequest(BaseModel):
    tg_id: int | None = None
    user_id: int | None = None
    id: int

@router.post("/campaigns/delete")
def delete_campaign(p: CampaignDeleteRequest):
    with SessionLocal() as db:
        uid = _resolve_user_id(db, p.tg_id, p.user_id)

        row = db.execute(
            text("DELETE FROM campaigns WHERE id=:cid AND user_id=:uid RETURNING id"),
            {"cid": p.id, "uid": uid},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="campaign not found")

        db.commit()
    return {"ok": True, "deleted_id": int(p.id)}
    
class CampaignSendNow(CampaignId):
    limit: int | None = None

@router.post("/campaigns/send_now")
def send_now(p: CampaignSendNow):
    with SessionLocal() as db:
        uid = _resolve_user_id(db, p.tg_id, p.user_id)
        camp = db.execute(text("""
            SELECT c.id, c.user_id, c.resume_id, c.saved_request_id, sr.query_params, sr.cover_letter
            FROM campaigns c
            JOIN saved_requests sr ON sr.id = c.saved_request_id
            WHERE c.id=:cid AND c.user_id=:uid
            LIMIT 1
        """), {"cid": p.id, "uid": uid}).mappings().first()
        if not camp:
            raise HTTPException(404, "campaign not found")

        remaining = quota_for_user(db, uid)["remaining"]
        if remaining <= 0:
            return {"enqueued": 0, "remaining_quota": 0}
        
        first_batch = min(remaining, p.limit or FIRST_BATCH_DEFAULT)
        if first_batch <= 0:
            return {"enqueued": 0, "remaining_quota": remaining}
        qp = (camp["query_params"] or "").strip()
        vacancies = _hh_search_by_qs(db, uid, qp, limit=first_batch*3)

        # уже откликнутые этой кампанией (и вообще этим пользователем)
        existing = {
            str(v_id) for (v_id,) in db.execute(text("""
                SELECT vacancy_id FROM applications WHERE user_id=:u
            """), {"u": uid}).all()
        }

        enqueued = 0
        for v in vacancies:
            vid = str(v.get("id") or "").strip()
            if not vid or vid in existing:
                continue
            try:
                db.execute(text("""
                    INSERT INTO applications
                        (user_id, vacancy_id, status, source, meta, attempt_count, kind,
                         resume_id, campaign_id, cover_letter, created_at, updated_at)
                    VALUES
                        (:uid, :vid, 'queued', 'hh', '{}'::jsonb, 0, 'manual',
                         :rid, :cid, :cl, now(), now())
                    ON CONFLICT (user_id, vacancy_id) DO NOTHING
                """), {
                    "uid": uid,
                    "vid": vid,
                    "rid": camp["resume_id"],
                    "cid": camp["id"],
                    "cl":  camp.get("cover_letter") or None,
                })
                enqueued += 1
                if enqueued >= first_batch:
                    break
                existing.add(vid)
            except Exception:
                pass

        db.commit()
        return {"enqueued": enqueued, "remaining_quota": max(remaining - enqueued, 0)}

@router.post("/campaigns/auto_tick", response_model=dict)
def auto_tick(payload: Optional[dict] = Body(None)) -> dict:
    with SessionLocal() as db:
        active = db.execute(text("""
            SELECT c.id, c.user_id, c.resume_id, sr.query_params, sr.cover_letter
            FROM campaigns c
            JOIN saved_requests sr ON sr.id = c.saved_request_id
            WHERE c.status = 'active'
            ORDER BY c.id DESC
            LIMIT 200
        """)).mappings().all()

        total_enq = 0
        start_utc, end_utc = today_bounds_msk()  

        for camp in active:
            uid = int(camp["user_id"])

            # 1) Остаток по успешным отправкам (sent) — из admin_today_quotas
            q = quota_for_user(db, uid)
            remaining_success = int(q["remaining"])
            if remaining_success <= 0:
                continue

            # 2) Сколько уже стоит в очереди/ретрае сегодня (только kind='auto')
            inflight_auto = db.execute(text("""
                SELECT COUNT(*)::int
                FROM applications
                WHERE user_id = :u
                  AND kind = 'auto'
                  AND status IN ('queued','retry')
                  AND created_at >= :start_utc AND created_at < :end_utc
            """), {"u": uid, "start_utc": start_utc, "end_utc": end_utc}).scalar() or 0

            to_enqueue = max(0, remaining_success - inflight_auto)
            if to_enqueue <= 0:
                continue

            qp = (camp["query_params"] or "").strip()
            vacancies = _hh_search_by_qs(db, uid, qp, limit=to_enqueue * 2)

            existing = {
                str(v_id) for (v_id,) in db.execute(text("""
                    SELECT vacancy_id FROM applications WHERE user_id = :u
                """), {"u": uid}).all()
            }

            enq = 0
            for v in vacancies:
                vid = str(v.get("id") or "").strip()
                if not vid or vid in existing:
                    continue
                try:
                    db.execute(text("""
                        INSERT INTO applications
                          (user_id, vacancy_id, status, source, meta, attempt_count, kind,
                           resume_id, campaign_id, cover_letter, created_at, updated_at)
                        VALUES
                          (:uid, :vid, 'queued', 'hh', '{}'::jsonb, 0, 'auto',
                           :rid, :cid, :cl, now(), now())
                        ON CONFLICT (user_id, vacancy_id) DO NOTHING
                    """), {
                        "uid": uid,
                        "vid": vid,
                        "rid": camp["resume_id"],
                        "cid": camp["id"],
                        "cl": camp.get("cover_letter") or None,
                    })
                    enq += 1
                    if enq >= to_enqueue:
                        break
                    existing.add(vid)
                except Exception:
                    pass

            total_enq += enq

        db.commit()
        return {"enqueued": int(total_enq)}