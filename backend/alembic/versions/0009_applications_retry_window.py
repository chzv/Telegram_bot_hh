from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0009_applications_retry_window"
down_revision = "0004_applications"  
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)

    cols = {c["name"] for c in insp.get_columns("applications")}
    if "attempt_count" not in cols:
        op.add_column("applications", sa.Column("attempt_count", sa.SmallInteger, nullable=False, server_default="0"))
    if "next_try_at" not in cols:
        op.add_column("applications", sa.Column("next_try_at", sa.TIMESTAMP(timezone=True), nullable=True))

    op.execute("CREATE INDEX IF NOT EXISTS ix_applications_next_try ON applications (next_try_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_applications_status ON applications (status)")

def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_applications_next_try")
    op.execute("DROP INDEX IF EXISTS ix_applications_status")
    op.drop_column("applications", "next_try_at")
    op.drop_column("applications", "attempt_count")
