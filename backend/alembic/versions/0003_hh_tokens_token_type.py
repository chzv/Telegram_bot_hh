"""add token_type to hh_tokens (idempotent)"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0003_hh_tokens_token_type"
down_revision = "0002_core"
branch_labels = None
depends_on = None

def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("hh_tokens")}
    if "token_type" not in cols:
        op.add_column(
            "hh_tokens",
            sa.Column("token_type", sa.String(length=32), nullable=False, server_default="bearer"),
        )
        op.alter_column("hh_tokens", "token_type", server_default=None)

def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("hh_tokens")}
    if "token_type" in cols:
        op.drop_column("hh_tokens", "token_type")
