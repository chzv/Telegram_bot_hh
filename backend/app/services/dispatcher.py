# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import re
from typing import Optional

import httpx
from sqlalchemy import text

from app.db import SessionLocal
from app.services.hh_client import (
    send_response, HHError, HHUnauthorized, HHAlreadyApplied, HHNonRetryable
)
from app.services.limits import quota_for_user, today_bounds_msk
from app.services.notifier import notify_quota_exhausted_once

import logging
import json

# --- параметры воркера ---
BATCH_SIZE = 50
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = [60, 300, 900, 3600, 86400]  # 1м, 5м, 15м, 1ч, 24ч


def _backoff(attempt: int) -> int:
    i = max(0, min(attempt, len(BACKOFF_SECONDS) - 1))
    return BACKOFF_SECONDS[i]


def _need_retry_from_msg(msg: str) -> bool:
    """
    429/5xx — явно ретраимые.
    Всё остальное — нет (400 и прочее считаем логическими ошибками).
    """
    return bool(re.search(r"\b(429|5\d{2})\b", msg))


def _is_vacancy_not_found(msg: str) -> bool:
    return (
        "Vacancy not found" in msg
        or "vacancy_not_found" in msg
        or '"type":"not_found"' in msg
    )


def _is_unauthorized(msg: str) -> bool:
    return " 401" in msg or "unauthorized" in msg

def _classify_reason(msg: str) -> str | None:
    """
    Возвращает короткую причину пропуска:
    - 'test_required'      — «Нужно пройти тест»
    - 'letter_required'    — «Требуется сопроводительное»
    - 'vacancy_not_found'  — вакансия удалена/не найдена
    Иначе None.
    """
    if not msg:
        return None

    low = msg.lower()

    # Быстрые эвристики по тексту
    if "test_required" in low or "must process test first" in low:
        return "test_required"

    if "letter required" in low:
        return "letter_required"

    if "vacancy not found" in low or "vacancy_not_found" in low or '"type":"not_found"' in low:
        return "vacancy_not_found"

    # Иногда HH кладёт полезное только в JSON
    try:
        data = json.loads(msg)
        errs = data.get("errors") or []
        for e in errs:
            if e.get("type") == "negotiations" and e.get("value") == "test_required":
                return "test_required"
        for ba in data.get("bad_arguments") or []:
            if (ba.get("name") or "").lower() == "message":
                return "letter_required"
    except Exception:
        pass

    return None
    
async def dispatch_once(dry_run: bool = False, limit: int = BATCH_SIZE) -> dict:
    taken = sent = retried = failed = skipped = 0

    with SessionLocal() as db:
        rows = db.execute(text("""
            SELECT id, user_id, vacancy_id, resume_id, cover_letter, attempt_count
              FROM applications
             WHERE
               (status = 'queued' AND COALESCE(next_try_at, now()) <= now())
                OR
               (status = 'retry'  AND next_try_at <= now())
             ORDER BY id
             LIMIT :lim
        """), {"lim": limit}).mappings().all()

        taken = len(rows)

        for r in rows:
            app_id = r["id"]
            try:
                if dry_run:
                    skipped += 1
                    continue

                # токен
                tok = db.execute(
                    text("SELECT access_token FROM hh_tokens WHERE user_id=:uid"),
                    {"uid": r["user_id"]},
                ).first()
                if not tok or not tok[0]:
                    db.execute(text("""
                        UPDATE applications
                           SET status='error',
                               error='no hh access_token for user',
                               updated_at=now()
                         WHERE id=:id
                    """), {"id": app_id})
                    failed += 1
                    continue
                q = quota_for_user(db, r["user_id"])
                if q["remaining"] <= 0:
                    # ставим на начало следующего дня по МСК
                    _, end_utc = today_bounds_msk()  # конец сегодняшних суток по МСК в UTC
                    db.execute(text("""
                        UPDATE applications
                           SET status='retry',
                               next_try_at = :nta,
                               updated_at = now()
                         WHERE id = :id
                    """), {"id": app_id, "nta": end_utc})
                    notify_quota_exhausted_once(db, r["user_id"], q["reset_time"], q["tariff"])
                    skipped += 1
                    continue
                # попытка отправки
                try:
                    await send_response(
                        access_token=tok[0],
                        vacancy_id=int(r["vacancy_id"]),
                        resume_id=str(r["resume_id"]),
                        cover_letter=r["cover_letter"] or None,
                    )
                except HHAlreadyApplied as e:
                # считаем успехом
                    db.execute(text("""
                        UPDATE applications
                           SET status='sent',
                               sent_at=COALESCE(sent_at, now()),
                               error=:er,
                               updated_at=now()
                         WHERE id=:id
                    """), {"id": app_id, "er": f"already_applied: {str(e)[:400]}"})
                    sent += 1
                except HHNonRetryable as e:
                    msg = str(e)
                    reason = _classify_reason(msg)
                    if reason in {"test_required", "letter_required", "vacancy_not_found"}:
                        logging.info(
                            "[apply] skipped user=%s vacancy=%s reason=%s (non-retryable)",
                            r["user_id"], r["vacancy_id"], reason
                        )
                        db.execute(text("""
                            UPDATE applications
                               SET status='error',
                                   error=:reason,
                                   updated_at=now()
                             WHERE id=:id
                        """), {"id": app_id, "reason": reason})
                        skipped += 1
                    else:
                        db.execute(text("""
                            UPDATE applications
                               SET status='error',
                                   error=:er,
                                   updated_at=now()
                             WHERE id=:id
                        """), {"id": app_id, "er": f"non-retryable: {msg[:500]}"})
                        failed += 1
                except HHUnauthorized as e:
                    # авторизация — быстрый ретрай (можно вставить refresh_access_token())
                    attempt = int(r["attempt_count"] or 0) + 1
                    delay = _backoff(max(0, attempt - 1))
                    next_try = datetime.utcnow() + timedelta(seconds=delay)
                    db.execute(text("""
                        UPDATE applications
                           SET status='retry',
                               error=:er,
                               attempt_count=:ac,
                               next_try_at=:nta,
                               updated_at=now()
                         WHERE id=:id
                    """), {"id": app_id, "er": f"401 unauthorized: {str(e)[:450]}", "ac": attempt, "nta": next_try})
                    retried += 1
                except HHError as e:
                    msg = str(e)
                    reason = _classify_reason(msg)
                    if reason in {"test_required", "letter_required", "vacancy_not_found"}:
                        logging.info(
                            "[apply] skipped user=%s vacancy=%s reason=%s",
                            r["user_id"], r["vacancy_id"], reason
                        )
                        db.execute(text("""
                            UPDATE applications
                               SET status='error',
                                   error=:reason,
                                   updated_at=now()
                             WHERE id=:id
                        """), {"id": app_id, "reason": reason})
                        skipped += 1
                    else:
                        attempt = int(r["attempt_count"] or 0) + 1
                        if attempt >= MAX_ATTEMPTS:
                            db.execute(text("""
                                UPDATE applications
                                   SET status='error',
                                       error=:er,
                                       attempt_count=:ac,
                                       updated_at=now()
                                 WHERE id=:id
                            """), {"id": app_id, "er": f"max attempts; last: {msg[:500]}", "ac": attempt})
                            failed += 1
                        else:
                            delay = _backoff(attempt - 1)
                            next_try = datetime.utcnow() + timedelta(seconds=delay)
                            db.execute(text("""
                                UPDATE applications
                                   SET status='retry',
                                       error=:er,
                                       attempt_count=:ac,
                                       next_try_at=:nta,
                                       updated_at=now()
                                 WHERE id=:id
                            """), {"id": app_id, "er": msg[:500], "ac": attempt, "nta": next_try})
                            retried += 1
    
                else:
                    # успех
                    db.execute(text("""
                        UPDATE applications
                           SET status='sent',
                               sent_at=now(),
                               error=NULL,
                               updated_at=now()
                         WHERE id=:id
                    """), {"id": app_id})
                    sent += 1
            except Exception as e:
                # неожиданные — в ретрай/ошибку по лимиту
                attempt = int(r.get("attempt_count") or 0) + 1
                if attempt >= MAX_ATTEMPTS:
                    db.execute(text("""
                        UPDATE applications
                           SET status='error',
                               error=:er,
                               attempt_count=:ac,
                               updated_at=now()
                         WHERE id=:id
                    """), {"id": app_id, "er": f"unexpected: {str(e)[:500]}", "ac": attempt})
                    failed += 1
                else:
                    delay = _backoff(attempt - 1)
                    next_try = datetime.utcnow() + timedelta(seconds=delay)
                    db.execute(text("""
                        UPDATE applications
                           SET status='retry',
                               error=:er,
                               attempt_count=:ac,
                               next_try_at=:nta,
                               updated_at=now()
                         WHERE id=:id
                    """), {"id": app_id, "er": f"unexpected: {str(e)[:500]}", "ac": attempt, "nta": next_try})
                    retried += 1

        db.commit()

    return {
        "taken": taken,
        "sent": sent,
        "retried": retried,
        "failed": failed,
        "skipped": skipped,
    }


async def run_loop(sleep_sec: int = 5, dry_run: bool = False):
    while True:
        stats = await dispatch_once(dry_run=dry_run)
        print(f"[dispatcher] {stats}")
        await asyncio.sleep(sleep_sec)


if __name__ == "__main__":
    asyncio.run(run_loop(dry_run=True))
