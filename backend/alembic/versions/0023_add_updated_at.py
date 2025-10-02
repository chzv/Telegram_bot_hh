"""add updated_at to applications"""

from alembic import op
import sqlalchemy as sa

revision = "0023_add_updated_at"
down_revision = "0022_add_resume_id"  
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='applications' AND column_name='updated_at'
      ) THEN
        ALTER TABLE applications
          ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
      END IF;
    END $$;
    """)


def downgrade() -> None:
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='applications' AND column_name='updated_at'
      ) THEN
        ALTER TABLE applications DROP COLUMN updated_at;
      END IF;
    END $$;
    """)
