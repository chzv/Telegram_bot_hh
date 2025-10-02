# backend/app/services/notifier.py
from __future__ import annotations

import os
import time
import json
import socket
from datetime import datetime, timezone, timedelta
from app.services.limits import today_bounds_msk
from typing import Iterable, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from urllib.request import Request, urlopen
from urllib.parse import urlencode

# --- Telegram ---

BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage" if BOT_TOKEN else None

# куда вести кнопки оплаты
BACKEND_BASE = (os.getenv("BACKEND_BASE_URL") or "https://api.hhofferbot.ru").rstrip("/")

# --- DB engine (sync) ---
def _build_sync_dsn() -> str:
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    dsn = (
        dsn.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
        .replace("postgresql://", "postgresql+psycopg2://", 1)
    )
    try:
        url = make_url(dsn)
        host = url.host or ""
        if host and not os.path.exists("/.dockerenv"):
            socket.gethostbyname(host)
    except Exception:
        pass
    return dsn


_engine = create_engine(_build_sync_dsn(), pool_pre_ping=True, future=True)


# --- helpers ---
def _payment_keyboard(tg_id: int) -> dict:
    """Инлайн-кнопки оплаты для конкретного пользователя."""
    week = f"{BACKEND_BASE}/pay?plan=week&tg_id={int(tg_id)}"
    month = f"{BACKEND_BASE}/pay?plan=month&tg_id={int(tg_id)}"
    return {
        "inline_keyboard": [
            [{"text": "Неделя — 690₽", "url": week}],
            [{"text": "Месяц — 1900₽", "url": month}],
        ]
    }


def _needs_payment_keyboard(text_msg: str) -> bool:
    """Эвристика — если в тексте есть /payment или упоминание оплаты, подставим кнопки."""
    t = (text_msg or "").lower()
    return "/payment" in t or "оплат" in t


def _tg_send(tg_id: int, text_msg: str, reply_markup: Optional[dict] = None) -> None:
    """Отправка plain-text (4096 ограничение Telegram — режем на части)."""
    if not API_URL:
        raise RuntimeError("BOT_TOKEN/TELEGRAM_BOT_TOKEN not set")
    if not text_msg:
        return

    chunks: List[str] = [text_msg[i : i + 4096] for i in range(0, len(text_msg), 4096)] or [text_msg]
    for idx, part in enumerate(chunks):
        payload = {
            "chat_id": int(tg_id),
            "text": part,
            "disable_web_page_preview": True,
        }
        # reply_markup отправляем только с первой частью
        if reply_markup and idx == 0:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        data = urlencode(payload).encode("utf-8")
        req = Request(API_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urlopen(req, timeout=15) as resp:  # noqa: S310
            if resp.status != 200:
                body = resp.read().decode("utf-8", "ignore")
                raise RuntimeError(f"tg {resp.status}: {body}")
        time.sleep(0.05)  # anti rate-limit fan-out


def _get_user_tg(conn, user_id: int) -> Optional[int]:
    row = conn.execute(text("SELECT tg_id FROM users WHERE id=:id"), {"id": user_id}).first()
    return int(row[0]) if row and row[0] else None


def _iter_all_tg(conn, batch: int = 500) -> Iterable[int]:
    q = text("SELECT tg_id FROM users WHERE tg_id IS NOT NULL")
    for row in conn.execute(q).yield_per(batch):
        if row[0]:
            yield int(row[0])


def _mark(nid: int, status: str, error: str | None = None):
    with _engine.begin() as conn:
        if status == "sent":
            conn.execute(
                text(
                    """
                UPDATE notifications
                   SET status='sent', sent_at=now(), error=NULL, updated_at=now()
                 WHERE id=:id
                """
                ),
                {"id": nid},
            )
        elif status == "failed":
            conn.execute(
                text(
                    """
                UPDATE notifications
                   SET status='failed', error=:err, updated_at=now()
                 WHERE id=:id
                """
                ),
                {"id": nid, "err": (error or "")[:1000]},
            )
        else:
            conn.execute(
                text("UPDATE notifications SET status=:st, updated_at=now() WHERE id=:id"),
                {"id": nid, "st": status},
            )


def _select_pending(limit: int = 50):
    """
    Берём уведомления, запланированные к отправке (ручные и авто — одинаково).
    """
    sql = text(
        """
        SELECT id, scope, user_id, text
          FROM notifications
         WHERE status='pending' AND scheduled_at <= now()
         ORDER BY scheduled_at ASC
         LIMIT :lim
         FOR UPDATE SKIP LOCKED
        """
    )
    with _engine.begin() as conn:
        rows = conn.execute(sql, {"lim": limit}).mappings().all()
    return rows


# --- автонапоминания по подпискам ---

def _plural_days_ru(n: int) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} день"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return f"{n} дня"
    return f"{n} дней"


def _ceil_days_left(expires_at: datetime, now_dt: datetime) -> int:
    # «потолок» оставшихся дней
    seconds = (expires_at - now_dt).total_seconds()
    return max(int((seconds + 86399) // 86400), 0)


def _enqueue(conn, user_id: int, text_msg: str) -> int:
    """
    Кладём запись в notifications (видно в админке).
    Возвращает id созданного уведомления.
    """
    row = conn.execute(
        text(
            """
        INSERT INTO notifications (user_id, scope, text, scheduled_at, status)
        VALUES (:uid, 'user', :txt, now(), 'pending')
        RETURNING id
        """
        ),
        {"uid": int(user_id), "txt": text_msg},
    ).first()
    return int(row[0])


def _schedule_subscription_reminders() -> int:
    """
    Находит подписки с остатком 3 или 1 день и просроченные,
    и для тех, по кому ещё не слали соответствующее напоминание,
    создаёт записи в notifications + помечает в subscription_notifications.

    Возвращает число созданных уведомлений.
    """
    created = 0
    now_dt = datetime.now(timezone.utc)

    with _engine.begin() as conn:
        # Кандидаты для D3/D1 (попадающие в ближайшие 4 дня) + уже просроченные
        subs = conn.execute(
            text(
                """
            SELECT s.id, s.user_id, s.expires_at, s.status
              FROM subscriptions s
             WHERE s.status IN ('active', 'expired')
               AND s.expires_at IS NOT NULL
               AND s.expires_at <= (now() AT TIME ZONE 'utc') + interval '4 days'
            """
            )
        ).mappings().all()

        for s in subs:
            sid = int(s["id"])
            uid = int(s["user_id"])
            exp: datetime = s["expires_at"]
            exp = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)

            if exp <= now_dt:
                # Просрочено — уведомление EXPIRED (разово)
                if s["status"] == "active":
                    conn.execute(
                        text("UPDATE subscriptions SET status='expired' WHERE id=:sid"),
                        {"sid": sid},
                    )
                ins = conn.execute(
                    text(
                        """
                    INSERT INTO subscription_notifications (subscription_id, kind)
                    VALUES (:sid, 'EXPIRED')
                    ON CONFLICT (subscription_id, kind) DO NOTHING
                    RETURNING id
                    """
                    ),
                    {"sid": sid},
                ).first()
                if ins:
                    text_msg = (
                        "⚠️ Подписка закончилась.\n"
                        "Ваш лимит откликов: 10 в сутки\n"
                        "Верните 200 откликов в сутки → /payment"
                    )
                    _enqueue(conn, uid, text_msg)
                    created += 1
                continue

            # Ещё активна — считаем «ceil days left»
            days_left = _ceil_days_left(exp, now_dt)
            if days_left not in (3, 1):
                continue

            kind = "D3" if days_left == 3 else "D1"
            ins = conn.execute(
                text(
                    """
                INSERT INTO subscription_notifications (subscription_id, kind)
                VALUES (:sid, :kind)
                ON CONFLICT (subscription_id, kind) DO NOTHING
                RETURNING id
                """
                ),
                {"sid": sid, "kind": kind},
            ).first()
            if not ins:
                continue  # уже делали

            text_msg = (
                f"⚠️ Подписка заканчивается через {_plural_days_ru(days_left)}.\n"
                "Чтобы не потерять лимит 200 откликов в сутки — продлите сейчас → /payment"
            )
            _enqueue(conn, uid, text_msg)
            created += 1

    return created


# --- основной цикл ---
def run_once() -> int:
    """
    1) Планируем автонапоминания по подпискам (кладём в notifications).
    2) Отправляем pending-уведомления (и ручные, и авто).
    Возвращает число реально отправленных сообщений.
    """
    try:
        _schedule_subscription_reminders()
    except Exception as e:
        print("[notifier] schedule error:", e)

    rows = _select_pending(limit=25)
    if not rows:
        return 0

    sent_any = 0
    with _engine.begin() as conn:
        for n in rows:
            nid, scope, user_id, textmsg = n["id"], n["scope"], n["user_id"], n["text"]
            try:
                if scope == "user":
                    tg = _get_user_tg(conn, int(user_id)) if user_id else None
                    if not tg:
                        raise RuntimeError("user has no tg_id")
                    kb = _payment_keyboard(tg) if _needs_payment_keyboard(textmsg) else None
                    _tg_send(tg, textmsg, reply_markup=kb)
                else:  
                    for tg in _iter_all_tg(conn):
                        _tg_send(tg, textmsg)  
                        time.sleep(0.02)
                _mark(nid, "sent")
                sent_any += 1
            except Exception as e:
                _mark(nid, "failed", error=str(e))
    return sent_any

def _already_notified_today(db: Session, user_id: int, marker: str) -> bool:
    start_utc, _ = today_bounds_msk()
    row = db.execute(text("""
        SELECT 1
          FROM notifications
         WHERE user_id = :u
           AND created_at >= :start_utc
           AND status IN ('pending','queued','sent')
           AND text ILIKE :pat
         LIMIT 1
    """), {"u": user_id, "start_utc": start_utc, "pat": f"%{marker}%"}).first()
    return bool(row)

def enqueue(db: Session, user_id: int, text_body: str) -> None:
    db.execute(text("""
        INSERT INTO notifications(user_id, scope, text, status, scheduled_at, created_at, updated_at)
        VALUES (:u, 'user', :t, 'pending', now(), now(), now())
    """), {"u": user_id, "t": text_body})

def notify_quota_exhausted_once(db: Session, user_id: int, reset_time_str: str, tariff: str) -> None:
    """
    Отправляет сообщение об исчерпании лимита ровно один раз в сутки.
    Разные тексты для free/paid (как в ТЗ).
    """
    marker = "Дневной лимит откликов и автооткликов исчерпан"
    if _already_notified_today(db, user_id, marker):
        return

    if tariff == "free":
        body = (
            f"⏳ Дневной лимит откликов и автооткликов исчерпан.\n"
            f"Лимит обновится в {reset_time_str} (МСК).\n\n"
            f"Увеличьте лимит до 200 откликов в день. Подписка → /payment"
        )
    else:
        body = (
            f"⏳ Дневной лимит откликов и автооткликов исчерпан.\n"
            f"Лимит обновится в {reset_time_str} (МСК)."
        )
    enqueue(db, user_id, body)
def start_loop():
    """
    Бесконечный цикл (включается флагом ENABLE_NOTIFIER=1).
    Работает и для ручных, и для автоуведомлений.
    """
    if not os.getenv("ENABLE_NOTIFIER"):
        return
    if not BOT_TOKEN:
        print("[notifier] BOT_TOKEN/TELEGRAM_BOT_TOKEN is not set; notifier disabled")
        return

    print("[notifier] started")
    while True:
        try:
            run_once()
        except Exception as e:
            print("[notifier] error:", e)
        time.sleep(15)  # период опроса
