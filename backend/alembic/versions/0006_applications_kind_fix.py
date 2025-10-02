"""ensure applications.kind with default + check"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "0006_applications_kind_fix"
down_revision = "0005_applications_patch"  
branch_labels = None
depends_on = None

def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if "applications" in insp.get_table_names():
        cols = {c["name"]: c for c in insp.get_columns("applications")}
        if "kind" not in cols:
            op.add_column("applications", sa.Column("kind", sa.String(length=16), nullable=True, server_default="manual"))
            bind.exec_driver_sql("UPDATE applications SET kind='manual' WHERE kind IS NULL")
            op.alter_column("applications", "kind", server_default=None, existing_type=sa.String(length=16))
            op.alter_column("applications", "kind", nullable=False, existing_type=sa.String(length=16))
        else:
            bind.exec_driver_sql("UPDATE applications SET kind='manual' WHERE kind IS NULL")
            try:
                op.alter_column("applications", "kind", nullable=False, existing_type=sa.String(length=16))
            except Exception:
                pass

        try:
            bind.exec_driver_sql("ALTER TABLE applications DROP CONSTRAINT IF EXISTS chk_app_kind")
        except Exception:
            pass
        try:
            bind.exec_driver_sql(
                "ALTER TABLE applications ADD CONSTRAINT chk_app_kind CHECK (kind IN ('manual','auto'))"
            )
        except Exception:
            pass

def downgrade() -> None:
    bind = op.get_bind()
    try:
        bind.exec_driver_sql("ALTER TABLE applications DROP CONSTRAINT IF EXISTS chk_app_kind")
    except Exception:
        pass