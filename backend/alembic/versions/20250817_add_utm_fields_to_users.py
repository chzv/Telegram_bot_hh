"""add utm fields to users (idempotent)"""

from alembic import op

revision = "20250817_add_utm_fields_to_users"
down_revision = "0017_subscriptions_and_tariffs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Колонки
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS utm_source   TEXT")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS utm_medium   TEXT")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS utm_campaign TEXT")
    # Индексы
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_utm_source   ON users (utm_source)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_utm_medium   ON users (utm_medium)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_utm_campaign ON users (utm_campaign)")


def downgrade() -> None:
    # Мягкий откат
    op.execute("DROP INDEX IF EXISTS idx_users_utm_campaign")
    op.execute("DROP INDEX IF EXISTS idx_users_utm_medium")
    op.execute("DROP INDEX IF EXISTS idx_users_utm_source")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS utm_campaign")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS utm_medium")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS utm_source")
