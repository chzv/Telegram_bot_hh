from alembic import op

revision = "0028_ref_percents_in_tariffs"
down_revision = "0027_referrals_full"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_name='tariffs' AND column_name='ref_p1_permille') THEN
        ALTER TABLE tariffs ADD COLUMN ref_p1_permille INT NOT NULL DEFAULT 200;
      END IF;
      IF NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_name='tariffs' AND column_name='ref_p2_permille') THEN
        ALTER TABLE tariffs ADD COLUMN ref_p2_permille INT NOT NULL DEFAULT 100;
      END IF;
      IF NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_name='tariffs' AND column_name='ref_p3_permille') THEN
        ALTER TABLE tariffs ADD COLUMN ref_p3_permille INT NOT NULL DEFAULT 50;
      END IF;
    END$$;
    """)

def downgrade():
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_name='tariffs' AND column_name='ref_p3_permille') THEN
        ALTER TABLE tariffs DROP COLUMN ref_p3_permille;
      END IF;
      IF EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_name='tariffs' AND column_name='ref_p2_permille') THEN
        ALTER TABLE tariffs DROP COLUMN ref_p2_permille;
      END IF;
      IF EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_name='tariffs' AND column_name='ref_p1_permille') THEN
        ALTER TABLE tariffs DROP COLUMN ref_p1_permille;
      END IF;
    END$$;
    """)
