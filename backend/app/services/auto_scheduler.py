# backend/app/services/auto_scheduler.py
from __future__ import annotations

import os
import asyncio
from datetime import datetime, time, timezone, timedelta
from typing import List, Any, Optional

import httpx
from sqlalchemy import text, bindparam

from app.db import SessionLocal
from app.services.limits import quota_for_user, TZ_MSK
from app.services.notifier import notify_quota_exhausted_once
from urllib.parse import parse_qsl, urlencode

HH_API = "https://api.hh.ru"
UA = "hhbot/1.0"


def _to_time(v: Any) -> time:
    """Принимает time | 'HH:MM' | любое → возвращает корректное time."""
    if isinstance(v, time):
        return v
    if isinstance(v, str):
        try:
            hh, mm = v.split(":")
            return time(int(hh), int(mm))
        except Exception:
            pass
    return time(9, 0)


def _sanitize_query(q: str | None) -> str:
    """Берём только часть после '?', убираем лишние префиксы и пробелы."""
    if not q:
        return ""
    return q.lstrip("?& ").strip()


async def _fetch_vacancy_ids(token: str, query: str, limit: int, date_from: Optional[str] = None) -> List[int]:
    """Возвращает до limit id вакансий, отсортированных по времени публикации.
       Если передан date_from (UTC ISO), добавляем его в запрос."""
    if limit <= 0:
        return []

    headers = {"Authorization": f"Bearer {token}", "User-Agent": UA}

    base_pairs: list[tuple[str, str]] = []
    if query:
        base_pairs = parse_qsl(query, keep_blank_values=True)

    pairs = [(k, v) for (k, v) in base_pairs if k not in {"order_by", "date_from"}]
    pairs.append(("order_by", "publication_time"))
    if date_from:
        pairs.append(("date_from", date_from))

    query_str = urlencode(pairs, doseq=True)

    out: List[int] = []
    page = 0
    per_page = 100
    async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
        while len(out) < limit and page < 10:
            url = f"{HH_API}/vacancies?{query_str}&page={page}&per_page={per_page}"
            r = await client.get(url)
            if r.status_code != 200:
                break
            items = r.json().get("items", [])
            if not items:
                break
            for it in items:
                try:
                    out.append(int(it["id"]))
                except Exception:
                    pass
                if len(out) >= limit:
                    break
            page += 1
    return out
    
async def dispatch_auto_once() -> dict:
    """Планирует авто-заявки по активным КАМПАНИЯМ и обновляет счётчики (учитывает суточную квоту пользователя)."""
    queued_total = 0
    poll_sec = int(os.getenv("AUTO_POLL_EVERY_SEC", "300"))  # 5 мин по умолчанию

    with SessionLocal() as db:
        # 0) Активные кампании + связанные saved_request
        campaigns = db.execute(text("""
            SELECT
                c.id                AS campaign_id,
                c.user_id           AS user_id,
                c.resume_id         AS resume_id,
                c.title             AS name,
                c.daily_limit       AS daily_limit,
                sr.query_params     AS query_params,
                sr.query            AS query,
                sr.area             AS area,
                sr.employment       AS employment,
                sr.schedule         AS schedule,
                sr.professional_roles AS professional_roles,
                sr.search_fields    AS search_fields,
                sr.cover_letter     AS cover_letter
            FROM campaigns c
            LEFT JOIN saved_requests sr ON sr.id = c.saved_request_id
            WHERE c.status = 'active'
        """)).mappings().all()

        now_t_msk = datetime.now(TZ_MSK).time()

        for r in campaigns:
            cid = r["campaign_id"]

            ok_resume = db.execute(text("""
                SELECT 1 FROM resumes WHERE user_id=:uid AND resume_id=:rid LIMIT 1
            """), {"uid": r["user_id"], "rid": r["resume_id"]}).scalar()
            if not ok_resume:
                continue

            # 2) Токен HeadHunter
            token = db.execute(text("SELECT access_token FROM hh_tokens WHERE user_id=:u"),
                               {"u": r["user_id"]}).scalar()
            if not token:
                continue

            # 3) Остатки: по кампании и по тарифу пользователя
            c_row = db.execute(text("""
                SELECT daily_limit, sent_today FROM campaigns WHERE id=:cid FOR UPDATE
            """), {"cid": cid}).mappings().first()
            if not c_row:
                continue
            remain_campaign = max(0, int(c_row["daily_limit"]) - int(c_row["sent_today"] or 0))

            q = quota_for_user(db, r["user_id"])
            if q["remaining"] <= 0:
                notify_quota_exhausted_once(db, r["user_id"], q["reset_time"], q["tariff"])
                continue
            user_remaining = max(0, int(q["remaining"]))

            allowed = min(remain_campaign, user_remaining) if remain_campaign > 0 else 0
            if allowed <= 0:
                continue

            # 4) От какой метки времени искать (UTC ISO)
            last_check = db.execute(text("""
                SELECT MAX(created_at)
                FROM applications
                WHERE campaign_id = :cid AND kind = 'auto'
            """), {"cid": cid}).scalar()

            start_of_day = datetime.now(TZ_MSK).replace(hour=0, minute=0, second=0, microsecond=0)
            since_dt = (last_check.astimezone(TZ_MSK) if last_check else start_of_day) - timedelta(seconds=2 * poll_sec)
            date_from_utc = since_dt.astimezone(timezone.utc).isoformat(timespec="seconds")

            # 5) Построить querystring 
            query = _sanitize_query(r.get("query_params"))
            if not query:
                parts: list[tuple[str, str]] = []
                if r.get("query"):
                    parts.append(("text", str(r["query"])))
                if r.get("area"):
                    try: parts.append(("area", str(int(r["area"])))); 
                    except Exception: pass
                for role in (r.get("professional_roles") or []):
                    try: parts.append(("professional_role", str(int(role))));
                    except Exception: pass
                for e in (r.get("employment") or []):
                    parts.append(("employment", str(e)))
                for s in (r.get("schedule") or []):
                    parts.append(("schedule", str(s)))
                for f in (r.get("search_fields") or []):
                    parts.append(("search_field", str(f)))
                query = urlencode(parts, doseq=True)
                if not query:
                    continue

            # 6) Получить вакансии
            ids = await _fetch_vacancy_ids(token, query, allowed, date_from=date_from_utc)

            inserted = 0
            if ids:
                ids = list(dict.fromkeys(int(v) for v in ids))[:allowed]

                existing = db.execute(text("""
                    SELECT vacancy_id
                    FROM applications
                    WHERE user_id = :uid AND vacancy_id = ANY(:vids)
                """), {"uid": r["user_id"], "vids": ids}).scalars().all()
                existing_set = set(int(x) for x in existing)
                to_insert = [int(v) for v in ids if int(v) not in existing_set]

                # Текст письма
                raw_cl = r.get("cover_letter")
                cl = (str(raw_cl).rstrip() if raw_cl is not None else "Здравствуйте! Откликаюсь на вакансию.")

                if to_insert:
                    stmt = text("""
                        WITH src(vid) AS (VALUES :vids),
                        ins AS (
                          INSERT INTO applications
                            (user_id, vacancy_id, resume_id, cover_letter, kind, status,
                             next_try_at, created_at, updated_at, campaign_id)
                          SELECT :uid, src.vid, :rid, :cl, 'auto', 'queued',
                                 NULL, now(), now(), :cid
                          FROM src
                          ON CONFLICT (user_id, vacancy_id) DO NOTHING
                          RETURNING 1
                        )
                        SELECT count(*) FROM ins
                    """).bindparams(bindparam("vids", expanding=True))

                    inserted = db.execute(
                        stmt,
                        {"uid": r["user_id"], "rid": r["resume_id"], "cid": cid, "vids": to_insert, "cl": cl},
                    ).scalar() or 0

            if inserted > 0:
                db.execute(text("""
                    UPDATE campaigns
                    SET sent_today = COALESCE(sent_today,0) + :n,
                        sent_total = COALESCE(sent_total,0) + :n,
                        updated_at = now()
                    WHERE id = :cid
                """), {"cid": cid, "n": inserted})

            queued_total += inserted

        db.commit()

    return {"queued": queued_total}


async def run_loop(interval_sec: int | None = None):
    if interval_sec is None:
        interval_sec = int(os.getenv("AUTO_POLL_EVERY_SEC", "300"))
    while True:
        stats = await dispatch_auto_once()
        print(f"[auto] {stats}")
        await asyncio.sleep(interval_sec)


if __name__ == "__main__":
    asyncio.run(run_loop())