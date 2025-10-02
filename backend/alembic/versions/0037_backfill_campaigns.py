"""backfill campaigns from auto_responses"""

from alembic import op
import sqlalchemy as sa
from datetime import date

revision = "0037_backfill_campaigns"
down_revision = "0036_add_campaigns"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    conn.execute(sa.text("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_campaigns_user_resume_request'
      ) THEN
        ALTER TABLE campaigns
        ADD CONSTRAINT uq_campaigns_user_resume_request
        UNIQUE (user_id, resume_id, saved_request_id);
      END IF;
    END
    $$;
    """))

    # 1) Создаём кампании из auto_responses: ВСЕ кампании как 'stopped'
    conn.execute(sa.text("""
        INSERT INTO campaigns (
            user_id, saved_request_id, resume_id, title,
            daily_limit, status, created_at, updated_at
        )
        SELECT
            ar.user_id,
            ar.saved_request_id,
            ar.resume_id,
            ar.name,
            ar.daily_limit,
            'stopped',              -- критично: не нарушаем uq_campaigns_user_active
            ar.created_at,
            ar.updated_at
        FROM auto_responses ar
        ON CONFLICT (user_id, resume_id, saved_request_id) DO NOTHING
    """))

    # 2) Если заявки сохранены — проставляем campaign_id 
    conn.execute(sa.text("""
        UPDATE applications a
        SET campaign_id = c.id
        FROM campaigns c
        WHERE a.user_id = c.user_id
          AND a.resume_id = c.resume_id
          AND a.campaign_id IS NULL
    """))

    # 3) Перенос статистики по auto_runs (суммарно и за сегодня)
    today = date.today().isoformat()
    conn.execute(sa.text(f"""
        WITH agg AS (
          SELECT
            ar.user_id,
            ar.resume_id,
            ar.saved_request_id,
            COALESCE(SUM(r.sent), 0) AS total,
            COALESCE(SUM(r.sent) FILTER (WHERE r.d = '{today}'), 0) AS today
          FROM auto_responses ar
          LEFT JOIN auto_runs r ON r.auto_id = ar.id
          GROUP BY ar.user_id, ar.resume_id, ar.saved_request_id
        )
        UPDATE campaigns c
        SET sent_total = agg.total,
            sent_today = agg.today
        FROM agg
        WHERE c.user_id = agg.user_id
          AND c.resume_id = agg.resume_id
          AND c.saved_request_id = agg.saved_request_id
    """))


def downgrade():
    conn = op.get_bind()

    conn.execute(sa.text("UPDATE applications SET campaign_id = NULL"))
    conn.execute(sa.text("UPDATE campaigns SET sent_total = 0, sent_today = 0"))
    conn.execute(sa.text("""
        ALTER TABLE campaigns
        DROP CONSTRAINT IF EXISTS uq_campaigns_user_resume_request;
    """))
