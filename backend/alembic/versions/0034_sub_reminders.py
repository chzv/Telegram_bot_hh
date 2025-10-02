from alembic import op
import sqlalchemy as sa

revision = "0034_sub_reminders"
down_revision = "0033_seed_tariffs"
branch_labels = None
depends_on = None

def upgrade():
    # индексы для быстрого поиска «скоро истекающих»
    op.create_index(
        "ix_subscriptions_status_expires",
        "subscriptions",
        ["status", "expires_at"],
        unique=False,
        schema=None,
        postgresql_include=None,
    )

    # таблица фиксирует «что уже отправили», чтобы шлётся один раз
    op.create_table(
        "subscription_notifications",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("subscription_id", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),  # D3|D1|EXPIRED
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("subscription_id", "kind", name="uq_subscr_notice_once"),
    )
    op.create_index(
        "ix_subscr_notice_subscription",
        "subscription_notifications",
        ["subscription_id"],
        unique=False,
    )

def downgrade():
    op.drop_index("ix_subscr_notice_subscription", table_name="subscription_notifications")
    op.drop_table("subscription_notifications")
    op.drop_index("ix_subscriptions_status_expires", table_name="subscriptions")