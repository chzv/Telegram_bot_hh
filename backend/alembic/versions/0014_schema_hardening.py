"""schema hardening: status not null, created_at timestamptz (idempotent)"""

from alembic import op
import sqlalchemy as sa

revision = "0014_schema_hardening"
down_revision = "0013_applications_indexes"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name='applications'
          AND column_name='status' AND is_nullable='YES'
      ) THEN
        ALTER TABLE applications ALTER COLUMN status SET NOT NULL;
      END IF;
    END $$;
    """)

    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name='applications'
          AND column_name='created_at' AND data_type='timestamp without time zone'
      ) THEN
        ALTER TABLE applications
          ALTER COLUMN created_at TYPE timestamptz
          USING (created_at AT TIME ZONE 'UTC');
      END IF;
    END $$;
    """)

def downgrade() -> None:
    op.execute("ALTER TABLE applications ALTER COLUMN status DROP NOT NULL;")
    op.execute("""
    ALTER TABLE applications
      ALTER COLUMN created_at TYPE timestamp without time zone
      USING (created_at AT TIME ZONE 'UTC');
    """)
