# backend/app/api/v1/admin_dashboard.py
from fastapi import APIRouter
import os, socket, re, datetime as dt
import psycopg2
from psycopg2 import errors

router = APIRouter(prefix="/admin", tags=["admin"])

import datetime as dt

def _date_range(from_str: str | None, to_str: str | None) -> tuple[dt.date, dt.date]:
    today = dt.date.today()
    if to_str:
        to_dt = dt.date.fromisoformat(to_str)
    else:
        to_dt = today
    if from_str:
        from_dt = dt.date.fromisoformat(from_str)
    else:
        # по умолчанию последние 30 дней, если не задано
        from_dt = to_dt - dt.timedelta(days=29)
    if from_dt > to_dt:
        from_dt, to_dt = to_dt, from_dt
    return from_dt, to_dt

@router.get("/charts/registrations")
def chart_registrations(from_date: str | None = None, to_date: str | None = None):
    """
    Динамика регистраций.
    - Если from/to не заданы — последние 30 дней.
    - Плюс 'total_all_time' для «в общем».
    """
    try:
        dsn = _dsn_pg()
        f, t = _date_range(from_date, to_date)
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT date::date, registrations::int
                    FROM metrics.registrations_daily
                    WHERE date BETWEEN %s AND %s
                    ORDER BY date
                    """,
                    (f, t),
                )
                rows = cur.fetchall()
                cur.execute("SELECT COALESCE(SUM(registrations),0) FROM metrics.registrations_daily")
                total_all_time = int(cur.fetchone()[0] or 0)
        return {
            "ok": True,
            "from": f.isoformat(),
            "to": t.isoformat(),
            "total_all_time": total_all_time,
            "days": [{"date": r[0].isoformat(), "count": int(r[1] or 0)} for r in rows],
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "days": [], "total_all_time": 0}

@router.get("/charts/subscribers/active")
def chart_active_subscribers(from_date: str | None = None, to_date: str | None = None):
    """
    Активные подписчики по дням за указанный диапазон (по умолчанию последние 30 дней).
    Использует VIEW metrics.active_subscribers_daily.
    """
    try:
        dsn = _dsn_pg()
        f, t = _date_range(from_date, to_date)
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT date::date, active_subscribers::int
                    FROM metrics.active_subscribers_daily
                    WHERE date BETWEEN %s AND %s
                    ORDER BY date
                    """,
                    (f, t),
                )
                rows = cur.fetchall()
        return {
            "ok": True,
            "from": f.isoformat(),
            "to": t.isoformat(),
            "days": [{"date": r[0].isoformat(), "count": int(r[1] or 0)} for r in rows],
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "days": []}

@router.get("/charts/funnel")
def chart_funnel_alltime():
    """
    Воронка за всё время (4 шага):
    visited → hh_connected → applied_20 → subscribed
    """
    try:
        dsn = _dsn_pg()
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT step, label, value::bigint
                    FROM metrics.funnel_alltime
                    ORDER BY step
                    """
                )
                rows = cur.fetchall()
        return {
            "ok": True,
            "steps": [{"key": s, "label": lbl, "value": int(v)} for (s, lbl, v) in rows],
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "steps": []}
# ====== /CHART ENDPOINTS ======

def _dsn_pg() -> str:
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        try:
            from app.core.config import settings
            dsn = (getattr(settings, "database_url", "") or "").strip()
        except Exception:
            pass
    if not dsn:
        raise RuntimeError("No DATABASE_URL found")
    if dsn.startswith("postgresql+psycopg2://"):
        dsn = dsn.replace("postgresql+psycopg2://", "postgresql://", 1)
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    # локальная разработка вне Docker: пробрасываем порт хоста
    if not os.path.exists("/.dockerenv"):
        try:
            socket.getaddrinfo("db", 5432)
        except Exception:
            host = "localhost"
            port = os.getenv("PGPORT_HOST", "5433")
            dsn = re.sub(r"@db(?::\d+)?", f"@{host}:{port}", dsn, count=1)
    return dsn

def _safe_scalar(cur, sql, params=None, default=0):
    try:
        cur.execute(sql, params or {})
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else default
    except errors.UndefinedTable:
        return default
    except Exception:
        return default

def _safe_sum(cur, sql, params=None, default=0.0):
    try:
        cur.execute(sql, params or {})
        row = cur.fetchone()
        v = row[0] if row else None
        return float(v) if v is not None else default
    except errors.UndefinedTable:
        return default
    except Exception:
        return default

def _safe_timeseries(cur, sql, params=None):
    """
    Возвращает список [{date:'YYYY-MM-DD', value:int}] с защитой от ошибок.
    """
    try:
        cur.execute(sql, params or {})
        rows = cur.fetchall() or []
        out = []
        for d, cnt in rows:
            if isinstance(d, dt.datetime):
                d = d.date()
            out.append({"date": d.isoformat(), "value": int(cnt or 0)})
        return out
    except errors.UndefinedTable:
        return []
    except Exception:
        return []

@router.get("/dashboard")
def admin_dashboard():
    try:
        dsn = _dsn_pg()
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                # === Заголовочные метрики ===
                users_total       = _safe_scalar(cur, "SELECT count(*) FROM users")
                users_new_7d      = _safe_scalar(cur, "SELECT count(*) FROM users WHERE created_at >= now() - interval '7 days'")
                users_new_today   = _safe_scalar(cur, "SELECT count(*) FROM users WHERE created_at::date = current_date")
                users_active_24h  = _safe_scalar(cur, "SELECT count(*) FROM users WHERE last_seen_at >= now() - interval '24 hours'")

                applications_total = _safe_scalar(cur, "SELECT count(*) FROM applications")
                applications_today = _safe_scalar(cur, "SELECT count(*) FROM applications WHERE created_at::date = current_date")
                applications_24h   = _safe_scalar(cur, "SELECT count(*) FROM applications WHERE created_at >= now() - interval '24 hours'")
                                # === Точки времени для сравнений ===
                now = dt.datetime.now(dt.timezone.utc)
                today = dt.date.today()
                yesterday = today - dt.timedelta(days=1)
                day_ago = now - dt.timedelta(days=1)
                two_days_ago = now - dt.timedelta(days=2)
                month_ago_date = today - dt.timedelta(days=30)

                # === Тренды пользователей ===
                # 1) Рост общей базы за месяц
                cur.execute("SELECT COUNT(*) FROM users WHERE created_at < %s", (month_ago_date,))
                users_total_month_ago = int(cur.fetchone()[0] or 0)
                users_total_month_pct = (
                    ((users_total - users_total_month_ago) / users_total_month_ago * 100.0)
                    if users_total_month_ago > 0 else (100.0 if users_total > 0 else 0.0)
                )

                # 2) Новые сегодня VS вчера
                cur.execute("SELECT COUNT(*) FROM users WHERE created_at::date = %s", (yesterday,))
                new_yesterday = int(cur.fetchone()[0] or 0)
                new_today_vs_yday_pct = (
                    ((users_new_today - new_yesterday) / new_yesterday * 100.0)
                    if new_yesterday > 0 else (100.0 if users_new_today > 0 else 0.0)
                )

                # 3) Активные за 24ч VS предыдущие 24ч
                cur.execute(
                    "SELECT COUNT(*) FROM users WHERE last_seen_at >= %s AND last_seen_at < %s",
                    (two_days_ago, day_ago),
                )
                active_prev_24h = int(cur.fetchone()[0] or 0)
                active_24h_vs_prev_pct = (
                    ((users_active_24h - active_prev_24h) / active_prev_24h * 100.0)
                    if active_prev_24h > 0 else (100.0 if users_active_24h > 0 else 0.0)
                )

                # === Тренды подписок ===
                # активные сейчас и сутки назад (снимок)
                cur.execute("""
                SELECT COUNT(DISTINCT user_id)
                FROM subscriptions
                WHERE status='active' AND started_at <= %s AND expires_at > %s
                """, (now, now))
                subs_active_now = int(cur.fetchone()[0] or 0)

                cur.execute("""
                SELECT COUNT(DISTINCT user_id)
                FROM subscriptions
                WHERE status='active' AND started_at <= %s AND expires_at > %s
                """, (day_ago, day_ago))
                subs_active_day_ago = int(cur.fetchone()[0] or 0)

                subs_active_vs_yday_pct = (
                    ((subs_active_now - subs_active_day_ago) / subs_active_day_ago * 100.0)
                    if subs_active_day_ago > 0 else (100.0 if subs_active_now > 0 else 0.0)
                )

                # === Тренды откликов ===
                # отклики за предыдущие 24ч
                cur.execute(
                    "SELECT COUNT(*) FROM applications WHERE created_at >= %s AND created_at < %s",
                    (two_days_ago, day_ago),
                )
                apps_prev_24h = int(cur.fetchone()[0] or 0)
                apps_24h_vs_prev_pct = (
                    ((applications_24h - apps_prev_24h) / apps_prev_24h * 100.0)
                    if apps_prev_24h > 0 else (100.0 if applications_24h > 0 else 0.0)
                )

                # среднее откликов на пользователя месяц назад (по пользователям, зарегистрированным до month_ago_date)
                cur.execute("SELECT COUNT(*) FROM users WHERE created_at < %s", (month_ago_date,))
                users_month_ago_total = int(cur.fetchone()[0] or 0)
                if users_month_ago_total > 0:
                    cur.execute("""
                        SELECT COUNT(*) FROM applications a
                        JOIN users u ON u.id = a.user_id
                        WHERE u.created_at < %s
                    """, (month_ago_date,))
                    apps_by_month_ago_users = int(cur.fetchone()[0] or 0)
                    avg_per_user_month_ago = apps_by_month_ago_users / users_month_ago_total
                else:
                    avg_per_user_month_ago = 0.0

                # распределение статусов откликов
                applications_by_status = {}
                try:
                    cur.execute("SELECT status, count(*) FROM applications GROUP BY status")
                    for status, cnt in cur.fetchall():
                        applications_by_status[str(status)] = int(cnt or 0)
                except Exception:
                    applications_by_status = {}

                # активные подписки
                subscriptions_active = _safe_scalar(
                    cur, "SELECT count(*) FROM subscriptions WHERE status = 'active'"
                )

                # платежи за 7д
                payments_7d_amount = _safe_sum(
                    cur, "SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='paid' AND created_at >= now() - interval '7 days'"
                )
                payments_7d_count  = _safe_scalar(
                    cur, "SELECT count(*) FROM payments WHERE status='paid' AND created_at >= now() - interval '7 days'"
                )

                # === Таймсерии для графиков (30 дней) ===
                registrations_30d = _safe_timeseries(cur, """
                    WITH days AS (
                      SELECT generate_series(current_date - interval '29 days', current_date, interval '1 day')::date AS d
                    ),
                    agg AS (
                      SELECT created_at::date AS d, count(*) AS cnt
                      FROM users
                      WHERE created_at::date >= current_date - interval '29 days'
                      GROUP BY 1
                    )
                    SELECT d, COALESCE(cnt,0)
                    FROM days LEFT JOIN agg USING(d)
                    ORDER BY d
                """)

                # Новые платящие пользователи в день:
                # 1) если есть subscriptions.created_at → берём её
                # 2) иначе — fallback по платежам (paid) в день
                subs_30d = _safe_timeseries(cur, """
                    DO $$ BEGIN END $$;  -- no-op для совместимости
                """)  # создаём переменную, заполним ниже

                try:
                    # пробуем по subscriptions.created_at
                    subs_30d = _safe_timeseries(cur, """
                        WITH days AS (
                          SELECT generate_series(current_date - interval '29 days', current_date, interval '1 day')::date AS d
                        ),
                        agg AS (
                          SELECT created_at::date AS d, count(*) AS cnt
                          FROM subscriptions
                          WHERE created_at::date >= current_date - interval '29 days'
                                AND status = 'active'
                          GROUP BY 1
                        )
                        SELECT d, COALESCE(cnt,0)
                        FROM days LEFT JOIN agg USING(d)
                        ORDER BY d
                    """)
                except Exception:
                    # fallback по платежам
                    subs_30d = _safe_timeseries(cur, """
                        WITH days AS (
                          SELECT generate_series(current_date - interval '29 days', current_date, interval '1 day')::date AS d
                        ),
                        agg AS (
                          SELECT created_at::date AS d, count(*) AS cnt
                          FROM payments
                          WHERE created_at::date >= current_date - interval '29 days'
                                AND status = 'paid'
                          GROUP BY 1
                        )
                        SELECT d, COALESCE(cnt,0)
                        FROM days LEFT JOIN agg USING(d)
                        ORDER BY d
                    """)

                # === Воронка конверсии ===
                hh_connected = _safe_scalar(
                    cur, "SELECT COUNT(DISTINCT user_id) FROM hh_tokens"
                )
                made_20_apps = _safe_scalar(cur, """
                    SELECT count(*) FROM (
                      SELECT user_id FROM applications GROUP BY user_id HAVING count(*) >= 20
                    ) t
                """)

        avg_per_user = (applications_total / users_total) if users_total else 0.0
        avg_per_user_month_pct = (
            ((avg_per_user - avg_per_user_month_ago) / avg_per_user_month_ago * 100.0)
            if avg_per_user_month_ago > 0 else (100.0 if avg_per_user > 0 else 0.0)
        )


        return {
            "ok": True,
            # верхние карточки
            "users": {
                "total": users_total,
                "new_7d": users_new_7d,
                "new_today": users_new_today,
                "active_24h": users_active_24h,
                "trend_total_month_pct": users_total_month_pct,
                "trend_new_today_vs_yday_pct": new_today_vs_yday_pct,
                "trend_active_24h_vs_prev_pct": active_24h_vs_prev_pct,
            },
            "applications": {
                "total": applications_total,
                "today": applications_today,
                "last_24h": applications_24h,
                "by_status": applications_by_status,
                "avg_per_user": avg_per_user,
                "trend_last_24h_vs_prev_pct": apps_24h_vs_prev_pct,
                "trend_avg_per_user_month_pct": avg_per_user_month_pct,
            },
            "subscriptions": {"active": subscriptions_active,
                              "trend_active_vs_yday_pct": subs_active_vs_yday_pct,},
            "finances": {"revenue_7d": payments_7d_amount, "payments_7d": payments_7d_count},
            # графики
            "charts": {
                "registrations_30d": registrations_30d,   # [{date, value}]
                "subscribers_30d":  subs_30d              # [{date, value}]
            },
            # воронка
            "funnel": {
                "visited": users_total,
                "hh_connected": hh_connected,
                "made_20_apps": made_20_apps,
                "paid": subscriptions_active
            }
        }
    except Exception as e:
        return {
            "ok": False, "error": str(e),
            "users": {"total": 0, "new_7d": 0, "new_today": 0, "active_24h": 0},
            "applications": {"total": 0, "today": 0, "last_24h": 0, "by_status": {}, "avg_per_user": 0.0},
            "subscriptions": {"active": 0},
            "finances": {"revenue_7d": 0.0, "payments_7d": 0},
            "charts": {"registrations_30d": [], "subscribers_30d": []},
            "funnel": {"visited": 0, "hh_connected": 0, "made_20_apps": 0, "paid": 0}
        }
