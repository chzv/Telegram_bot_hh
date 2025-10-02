from alembic import op
from sqlalchemy.engine import Connection

revision = "0016_export"
down_revision = "0015_referrals"
branch_labels = None
depends_on = None

def _exec(conn: Connection, sql: str):
    conn.exec_driver_sql(sql)

def upgrade():
    conn = op.get_bind()
    _exec(conn, """
    CREATE TABLE IF NOT EXISTS export_jobs (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'queued',
        file_path VARCHAR,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at TIMESTAMPTZ,
        CONSTRAINT fk_export_jobs_user
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """)
    _exec(conn, "CREATE INDEX IF NOT EXISTS ix_export_jobs_user ON export_jobs(user_id)")
    _exec(conn, "CREATE INDEX IF NOT EXISTS ix_export_jobs_status ON export_jobs(status)")

def downgrade():
    conn = op.get_bind()
    _exec(conn, "DROP INDEX IF EXISTS ix_export_jobs_status")
    _exec(conn, "DROP INDEX IF EXISTS ix_export_jobs_user")
    _exec(conn, "DROP TABLE IF EXISTS export_jobs")
