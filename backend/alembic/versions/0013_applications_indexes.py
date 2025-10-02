"""applications: runnable indexes and columns (idempotent)"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0013_applications_indexes"       
down_revision = "0012_fix_chk_app_status_retry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    # --- колонки ---
    cols = {c["name"] for c in insp.get_columns("applications")}

    with op.batch_alter_table("applications") as b:
        if "attempt_count" not in cols:
            b.add_column(sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False))
        if "next_try_at" not in cols:
            b.add_column(sa.Column("next_try_at", sa.TIMESTAMP(timezone=True), nullable=True))

    # --- индексы ---
    idx = {i["name"] for i in insp.get_indexes("applications")}

    if "ix_app_status_created" not in idx:
        op.create_index("ix_app_status_created", "applications", ["status", "created_at"])
    if "ix_app_user_status" not in idx:
        op.create_index("ix_app_user_status", "applications", ["user_id", "status"])
    if "ix_app_next_try_at" not in idx:
        op.create_index("ix_app_next_try_at", "applications", ["next_try_at"])

    # partial-индекс для ретраев – через raw SQL, с IF NOT EXISTS
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_app_retry_due
        ON applications (next_try_at)
        WHERE status = 'retry'
    """)


def downgrade() -> None:
    # удаляем аккуратно (если есть)
    op.execute("DROP INDEX IF EXISTS ix_app_retry_due;")
    op.drop_index("ix_app_next_try_at", table_name="applications")
    op.drop_index("ix_app_user_status", table_name="applications")
    op.drop_index("ix_app_status_created", table_name="applications")

    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("applications")}
    with op.batch_alter_table("applications") as b:
        if "next_try_at" in cols:
            b.drop_column("next_try_at")
        if "attempt_count" in cols:
            b.drop_column("attempt_count")
