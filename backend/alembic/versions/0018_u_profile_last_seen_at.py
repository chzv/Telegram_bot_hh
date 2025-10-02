from alembic import op
import sqlalchemy as sa

revision = "0018_u_profile_last_seen_at"
down_revision = "20250821_merge_heads"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
    ALTER TABLE users
      ADD COLUMN IF NOT EXISTS first_name   text,
      ADD COLUMN IF NOT EXISTS last_name    text,
      ADD COLUMN IF NOT EXISTS is_premium   boolean,
      ADD COLUMN IF NOT EXISTS lang         text,
      ADD COLUMN IF NOT EXISTS ref          text,
      ADD COLUMN IF NOT EXISTS last_seen_at timestamptz;
    """)

    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='users' AND column_name='last_seen'
      ) THEN
        UPDATE users
           SET last_seen_at = last_seen
         WHERE last_seen_at IS NULL AND last_seen IS NOT NULL;
      END IF;
    END $$;
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_hh_tokens_expires_at ON hh_tokens (expires_at);")

    op.execute("CREATE INDEX IF NOT EXISTS ix_users_username ON users (username);")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_users_username;")
    pass