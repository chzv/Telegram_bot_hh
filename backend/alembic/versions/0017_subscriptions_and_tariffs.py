"""subscriptions and tariffs tables (idempotent)"""

from alembic import op
import sqlalchemy as sa

# ревизии
revision = "0017_subscriptions_and_tariffs"
down_revision = "0016_export"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names(schema="public"))

    if "tariffs" not in existing:
        op.create_table(
            "tariffs",
            sa.Column("code", sa.String(64), primary_key=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("period_days", sa.Integer, nullable=False),
            sa.Column("price_minor", sa.Integer, nullable=False),
            sa.Column("currency", sa.String(8), nullable=False),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        )

    if "subscriptions" not in existing:
        op.create_table(
            "subscriptions",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("tariff_code", sa.String(64), sa.ForeignKey("tariffs.code"), nullable=False),
            sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        )
        op.create_index("ix_subscriptions_user_active", "subscriptions", ["user_id", "is_active"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names(schema="public"))

    if "subscriptions" in existing:
        try:
            op.drop_index("ix_subscriptions_user_active", table_name="subscriptions")
        except Exception:
            pass
        op.drop_table("subscriptions")

