# backend/app/api/v1/admin_analytics.py
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from datetime import datetime, timedelta
import os, socket

router = APIRouter(prefix="/admin/analytics", tags=["admin:analytics"])

def _build_sync_dsn() -> str:
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        try:
            from app.core.config import import_settings as _imp
            dsn = (getattr(_imp(), "database_url", "") or "").strip()
        except Exception:
            pass
    if not dsn:
        raise RuntimeError("No DATABASE_URL found")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1).replace("postgresql://", "postgresql+psycopg2://", 1)
    try:
        url = make_url(dsn)
        host = url.host or ""
        if host and not os.path.exists("/.dockerenv"):
            socket.gethostbyname(host)
    except Exception:
        pass
    return dsn

_engine = create_engine(_build_sync_dsn(), pool_pre_ping=True, future=True)

def _table_exists(conn, name: str) -> bool:
    q = text("select 1 from information_schema.tables where table_schema='public' and table_name=:t limit 1")
    return conn.execute(q, {"t": name}).first() is not None

def _applications_table(conn):
    # предпочитаем 'applications', иначе fallback на 'applications_queue'
    return "applications" if _table_exists(conn, "applications") else "applications_queue"

@router.get("/summary")
def admin_analytics_summary(days: int = Query(30, ge=7, le=180)):
    """Сводные метрики: рост пользователей, 30-дн удержание, средние отклики/день и недельная дельта."""
    now = datetime.utcnow()
    with _engine.begin() as conn:
        # рост пользователей
        q_cur = conn.execute(
            text("select count(*) from users where created_at >= (now() at time zone 'utc') - interval ':d days'".replace(":d", str(days)))
        ).scalar_one()
        q_prev = conn.execute(
            text("select count(*) from users where created_at >= (now() at time zone 'utc') - interval ':d days' * 2 and created_at < (now() at time zone 'utc') - interval ':d days'".replace(":d", str(days)))
        ).scalar_one()

        growth_delta = q_cur - q_prev
        growth_pct = (float(growth_delta) / q_prev * 100.0) if q_prev else (100.0 if q_cur > 0 else 0.0)

        # удержание (активны за 30д / все)
        active_30 = conn.execute(text("select count(*) from users where last_seen >= (now() at time zone 'utc') - interval '30 days'")).scalar_one()
        total_u   = conn.execute(text("select count(*) from users")).scalar_one()
        retention_30 = (float(active_30) / total_u * 100.0) if total_u else 0.0

        # отклики
        table = _applications_table(conn)
        # за 30д
        sent_30 = conn.execute(
            text(f"select count(*) from {table} where created_at >= (now() at time zone 'utc') - interval '30 days'")
        ).scalar_one()
        # пред. 7д и текущие 7д для недельной дельты
        sent_week_cur = conn.execute(
            text(f"select count(*) from {table} where created_at >= (now() at time zone 'utc') - interval '7 days'")
        ).scalar_one()
        sent_week_prev = conn.execute(
            text(f"select count(*) from {table} where created_at >= (now() at time zone 'utc') - interval '14 days' and created_at < (now() at time zone 'utc') - interval '7 days'")
        ).scalar_one()
        avg_per_day = round(sent_30 / 30.0, 2)
        week_delta_pct = (float(sent_week_cur - sent_week_prev) / sent_week_prev * 100.0) if sent_week_prev else (100.0 if sent_week_cur>0 else 0.0)

    return {
        "users": {"growth_pct": round(growth_pct,2), "delta": growth_delta, "period_days": days},
        "retention_30d": round(retention_30,2),
        "responses": {"avg_per_day": avg_per_day, "week_delta_pct": round(week_delta_pct,2)}
    }

@router.get("/activity-by-hour")
def admin_activity_by_hour(days: int = Query(30, ge=7, le=90)):
    """Гистограмма 0..23 по созданию откликов/заявок за Х дней."""
    with _engine.begin() as conn:
        table = _applications_table(conn)
        rows = conn.execute(text(
            f"""
            select extract(hour from created_at)::int as h, count(*) as c
            from {table}
            where created_at >= (now() at time zone 'utc') - interval ':d days'
            group by 1 order by 1
            """.replace(":d", str(days))
        )).all()
    buckets = [0]*24
    for h,c in rows:
        if 0<=h<24: buckets[h]=int(c)
    total = sum(buckets)
    return {"days": days, "buckets": buckets, "total": total}

@router.get("/top-users")
def admin_top_users(limit: int = Query(10, ge=1, le=50), days: int = Query(30, ge=7, le=180)):
    """ТОП пользователей по числу отправленных откликов за Х дней."""
    with _engine.begin() as conn:
        table = _applications_table(conn)
        rows = conn.execute(text(
            f"""
            select a.user_id,
                   count(*) as sent,
                   u.tg_id,
                   coalesce(u.hh_account_name,
                            nullif(trim(coalesce(u.first_name,''))||case when coalesce(u.last_name,'')<>'' then ' '||u.last_name else '' end,''),
                            u.username, u.email, u.id::text) as user_name
            from {table} a
            join users u on u.id = a.user_id
            where a.created_at >= (now() at time zone 'utc') - interval ':d days'
            group by a.user_id, u.tg_id, u.hh_account_name, u.first_name, u.last_name, u.username, u.email, u.id
            order by sent desc
            limit :lim
            """.replace(":d", str(days))
        ), {"lim": limit}).mappings().all()
    return {"days": days, "items": rows, "limit": limit}
