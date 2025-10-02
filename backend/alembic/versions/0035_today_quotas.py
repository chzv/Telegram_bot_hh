from alembic import op

revision = "0035_today_quotas"
down_revision = "0034_sub_reminders"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW admin_today_quotas AS
        WITH bounds AS (
          SELECT
            (date_trunc('day', (now() AT TIME ZONE 'Europe/Moscow')) AT TIME ZONE 'UTC') AS start_utc,
            ((date_trunc('day', (now() AT TIME ZONE 'Europe/Moscow')) + INTERVAL '1 day') AT TIME ZONE 'UTC') AS end_utc
        ),
        active_subs AS (
          SELECT DISTINCT user_id
          FROM subscriptions
          WHERE status = 'active' AND now() < expires_at
        ),
        sent_today AS (
          SELECT a.user_id, COUNT(*)::int AS used_today
          FROM applications a, bounds b
          WHERE a.status = 'sent'
            AND a.updated_at >= b.start_utc
            AND a.updated_at <  b.end_utc
          GROUP BY a.user_id
        ),
        all_users AS (
          SELECT user_id FROM applications
          UNION
          SELECT user_id FROM subscriptions
        )
        SELECT
          au.user_id,
          CASE WHEN s.user_id IS NOT NULL THEN 'paid' ELSE 'free' END AS tariff,
          CASE WHEN s.user_id IS NOT NULL THEN 200 ELSE 10 END AS tariff_limit,
          200 AS hard_cap,
          COALESCE(st.used_today, 0) AS used_today,
          GREATEST(
            0,
            LEAST(CASE WHEN s.user_id IS NOT NULL THEN 200 ELSE 10 END, 200) - COALESCE(st.used_today, 0)
          )::int AS remaining,
          TO_CHAR(
            (date_trunc('day', (now() AT TIME ZONE 'Europe/Moscow')) + INTERVAL '1 day'),
            'HH24:MI DD.MM.YYYY'
          ) AS reset_time_msk
        FROM all_users au
        LEFT JOIN active_subs s ON s.user_id = au.user_id
        LEFT JOIN sent_today  st ON st.user_id = au.user_id;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS admin_today_quotas;")