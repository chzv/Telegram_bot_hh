from alembic import op

revision = "0030_referral_indexes"
down_revision = "10e238349d4d"  
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
    WITH dups AS (
      SELECT
        MIN(id) AS keep_id,
        user_id, parent_user_id, level
      FROM referrals
      GROUP BY user_id, parent_user_id, level
      HAVING COUNT(*) > 1
    ),
    to_del AS (
      SELECT r.id
      FROM referrals r
      JOIN (
        SELECT user_id, parent_user_id, level
        FROM referrals
        GROUP BY user_id, parent_user_id, level
        HAVING COUNT(*) > 1
      ) g USING (user_id, parent_user_id, level)
      LEFT JOIN dups k
        ON k.user_id = r.user_id AND k.parent_user_id = r.parent_user_id AND k.level = r.level
      WHERE r.id <> k.keep_id
    )
    DELETE FROM referrals WHERE id IN (SELECT id FROM to_del);
    """)

    # 1) Уникальность рефкода — если колонки нет/пустая, код генерится в /generate и /me
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname='public' AND indexname='uq_users_ref_code'
      ) THEN
        CREATE UNIQUE INDEX uq_users_ref_code ON users(ref_code);
      END IF;
    END$$;
    """)

    # 2) Идемпотентность вставки в referrals 
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname='public' AND indexname='uq_ref_user_parent_level'
      ) THEN
        CREATE UNIQUE INDEX uq_ref_user_parent_level
          ON referrals(user_id, parent_user_id, level);
      END IF;
    END$$;
    """)

def downgrade():
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname='public' AND indexname='uq_ref_user_parent_level'
      ) THEN
        DROP INDEX uq_ref_user_parent_level;
      END IF;
    END$$;
    """)

    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname='public' AND indexname='uq_users_ref_code'
      ) THEN
        DROP INDEX uq_users_ref_code;
      END IF;
    END$$;
    """)
