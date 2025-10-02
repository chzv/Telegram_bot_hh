"""add nemiling_tarif_id to tariffs (idempotent)"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0003_tariffs_nemiling"
down_revision = "0002_core"
branch_labels = None
depends_on = None

def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if "tariffs" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("tariffs")}
        if "nemiling_tarif_id" not in cols:
            op.add_column(
                "tariffs",
                sa.Column("nemiling_tarif_id", sa.Integer(), nullable=True, unique=True),
            )

def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if "tariffs" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("tariffs")}
        if "nemiling_tarif_id" in cols:
            op.drop_column("tariffs", "nemiling_tarif_id")
