# backend/alembic/versions/002a_add_notifications.py
from alembic import op
import sqlalchemy as sa

revision = "41aec5f5cf8a"
down_revision = "21ba3213ffef"  
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.notifications (
      id            BIGSERIAL PRIMARY KEY,
      user_id       BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
      scope         TEXT NOT NULL DEFAULT 'user',        -- 'user' | 'all'
      text          TEXT NOT NULL,
      scheduled_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
      sent_at       TIMESTAMPTZ NULL,
      status        TEXT NOT NULL DEFAULT 'pending',     -- pending | queued | sent | canceled | failed
      error         TEXT NULL,
      created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE INDEX IF NOT EXISTS ix_notifications_scheduled
      ON public.notifications (status, scheduled_at);

    CREATE INDEX IF NOT EXISTS ix_notifications_user
      ON public.notifications (user_id);
    """)

def downgrade():
    op.execute("""
    DROP INDEX IF EXISTS ix_notifications_scheduled;
    DROP INDEX IF EXISTS ix_notifications_user;
    DROP TABLE IF EXISTS public.notifications;
    """)
