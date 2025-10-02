from alembic import op
import sqlalchemy as sa

revision = "0027_referrals_full"
down_revision = "0026_cover_letters_table"
branch_labels = None
depends_on = None

def upgrade():
    # users.ref_code (UNIQUE), users.referred_by (FK->users.id)
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='users' AND column_name='ref_code'
      ) THEN
        ALTER TABLE users ADD COLUMN ref_code TEXT UNIQUE;
      END IF;

      IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='users' AND column_name='referred_by'
      ) THEN
        ALTER TABLE users ADD COLUMN referred_by BIGINT NULL;
        CREATE INDEX IF NOT EXISTS ix_users_referred_by ON users(referred_by);
        ALTER TABLE users
          ADD CONSTRAINT fk_users_referred_by
          FOREIGN KEY (referred_by) REFERENCES users(id) ON DELETE SET NULL;
      END IF;
    END$$;
    """)

def downgrade():
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_name='users' AND column_name='referred_by') THEN
        ALTER TABLE users DROP CONSTRAINT IF EXISTS fk_users_referred_by;
        DROP INDEX IF EXISTS ix_users_referred_by;
        ALTER TABLE users DROP COLUMN referred_by;
      END IF;

      IF EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_name='users' AND column_name='ref_code') THEN
        ALTER TABLE users DROP COLUMN ref_code;
      END IF;
    END$$;
    """)
