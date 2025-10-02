from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import Connection

revision = "0015_referrals"
down_revision = "0014_schema_hardening"
branch_labels = None
depends_on = None

def _exec(conn: Connection, sql: str):
    conn.exec_driver_sql(sql)

def upgrade():
    conn = op.get_bind()

    _exec(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS ref_code VARCHAR(8)")
    _exec(conn, "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_ref_code ON users (ref_code)")

    _exec(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT")
    _exec(conn, """
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_users_referred_by') THEN
        ALTER TABLE users
          ADD CONSTRAINT fk_users_referred_by
          FOREIGN KEY (referred_by) REFERENCES users(id) NOT VALID;
      END IF;
    END $$;
    """)

    # referral_events
    _exec(conn, """
    CREATE TABLE IF NOT EXISTS referral_events (
      id BIGSERIAL PRIMARY KEY,
      user_id BIGINT NOT NULL,
      level SMALLINT NOT NULL,
      amount NUMERIC(12,2) NOT NULL DEFAULT 0,
      event VARCHAR(32) NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    _exec(conn, "CREATE INDEX IF NOT EXISTS ix_referral_events_user  ON referral_events(user_id)")
    _exec(conn, "CREATE INDEX IF NOT EXISTS ix_referral_events_level ON referral_events(level)")

    # проценты для тарифов
    _exec(conn, "ALTER TABLE tariffs ADD COLUMN IF NOT EXISTS ref_percent_l1 NUMERIC(5,2) DEFAULT 0")
    _exec(conn, "ALTER TABLE tariffs ADD COLUMN IF NOT EXISTS ref_percent_l2 NUMERIC(5,2) DEFAULT 0")
    _exec(conn, "ALTER TABLE tariffs ADD COLUMN IF NOT EXISTS ref_percent_l3 NUMERIC(5,2) DEFAULT 0")

def downgrade():
    conn = op.get_bind()
    _exec(conn, "DROP INDEX IF EXISTS ux_users_ref_code")
    _exec(conn, "ALTER TABLE users DROP COLUMN IF EXISTS ref_code")
    _exec(conn, """
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_users_referred_by') THEN
        ALTER TABLE users DROP CONSTRAINT fk_users_referred_by;
      END IF;
    END $$;
    """)
    _exec(conn, "ALTER TABLE users DROP COLUMN IF EXISTS referred_by")
    _exec(conn, "DROP TABLE IF EXISTS referral_events")
    _exec(conn, "ALTER TABLE tariffs DROP COLUMN IF EXISTS ref_percent_l1")
    _exec(conn, "ALTER TABLE tariffs DROP COLUMN IF EXISTS ref_percent_l2")
    _exec(conn, "ALTER TABLE tariffs DROP COLUMN IF EXISTS ref_percent_l3")
