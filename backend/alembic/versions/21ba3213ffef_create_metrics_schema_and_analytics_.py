"""create metrics schema and analytics views

Revision ID: 21ba3213ffef
Revises: 0029_sync_live_schema
Create Date: 2025-08-29 19:08:57.838997

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '21ba3213ffef'
down_revision: Union[str, Sequence[str], None] = '0029_sync_live_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.execute("""
        CREATE SCHEMA IF NOT EXISTS metrics;
    """)

    # 1) Регистрации по дням за весь горизонт
    op.execute("""
        CREATE OR REPLACE VIEW metrics.registrations_daily AS
        WITH bounds AS (
          SELECT
            LEAST((SELECT COALESCE(MIN(created_at), now())::date FROM public.users), current_date) AS d0,
            current_date AS d1
        ),
        days AS (
          SELECT generate_series((SELECT d0 FROM bounds), (SELECT d1 FROM bounds), INTERVAL '1 day')::date AS d
        )
        SELECT
          d::date AS date,
          COALESCE(COUNT(u.*), 0) AS registrations
        FROM days d
        LEFT JOIN public.users u ON u.created_at::date = d
        GROUP BY d
        ORDER BY d;
    """)

    # 2) Активные подписчики по дням за весь горизонт
    # активна на дату d, если started_at <= d < expires_at и status='active'
    op.execute("""
        CREATE OR REPLACE VIEW metrics.active_subscribers_daily AS
        WITH bounds AS (
          SELECT
            COALESCE(MIN(started_at), current_date)::date AS d0,
            GREATEST(current_date::date, COALESCE(MAX(expires_at), current_date)::date) AS d1
          FROM public.subscriptions
        ),
        days AS (
          SELECT generate_series((SELECT d0 FROM bounds), (SELECT d1 FROM bounds), INTERVAL '1 day')::date AS d
        )
        SELECT
          d::date AS date,
          COUNT(*) FILTER (
            WHERE s.started_at::date <= d
              AND s.expires_at::date  >  d
              AND s.status = 'active'
          ) AS active_subscribers
        FROM days d
        LEFT JOIN public.subscriptions s
          ON s.started_at::date <= d
        GROUP BY d
        ORDER BY d;
    """)

    # 3) Воронка за всё время (4 шага из макета)
    op.execute("""
        CREATE OR REPLACE VIEW metrics.funnel_alltime AS
        SELECT 'visited'          AS step, 'Зашли в бота'            AS label, COUNT(*)::bigint AS value
        FROM public.users
        UNION ALL
        SELECT 'hh_connected',       'Подключили HH',                COUNT(*)::bigint
        FROM public.users
        WHERE hh_account_id IS NOT NULL AND hh_account_id <> ''
        UNION ALL
        SELECT 'applied_20',         'Сделали 20 откликов',          COUNT(*)::bigint
        FROM (
          SELECT user_id, COUNT(*) AS cnt
          FROM public.applications
          WHERE status IN ('queued','sent')
          GROUP BY user_id
        ) t
        WHERE t.cnt >= 20
        UNION ALL
        SELECT 'subscribed',         'Оформили подписку (активна)',  COUNT(DISTINCT user_id)::bigint
        FROM public.subscriptions
        WHERE status = 'active'
          AND started_at <= now()
          AND expires_at  >  now();
    """)


def downgrade():
    op.execute("""DROP VIEW IF EXISTS metrics.funnel_alltime;""")
    op.execute("""DROP VIEW IF EXISTS metrics.active_subscribers_daily;""")
    op.execute("""DROP VIEW IF EXISTS metrics.registrations_daily;""")
    op.execute("""DROP SCHEMA IF EXISTS metrics;""")
