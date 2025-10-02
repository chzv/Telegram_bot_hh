"""add campaigns table and link to applications

Revision ID: 0036_add_campaigns
Revises: 0035_today_quotas
Create Date: 2025-09-13 23:59:00

"""
from alembic import op
import sqlalchemy as sa

revision = "0036_add_campaigns"
down_revision = "0035_today_quotas"
branch_labels = None
depends_on = None


def upgrade():
    # 1) campaigns
    op.create_table(
        "campaigns",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column(
            "saved_request_id",
            sa.BigInteger,
            sa.ForeignKey("saved_requests.id", ondelete="CASCADE"),
            nullable=True,
        ),
        # resume_id хранится как текст (у вас resumes.resume_id уникален и TEXT)
        sa.Column("resume_id", sa.Text, nullable=False),

        sa.Column("status", sa.String(length=16), nullable=False, server_default="stopped"),
        sa.Column("daily_limit", sa.Integer, nullable=False, server_default=sa.text("200")),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.TIMESTAMP(timezone=True), nullable=True),

        sa.Column("sent_total", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("sent_today", sa.Integer, nullable=False, server_default=sa.text("0")),

        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),

        sa.CheckConstraint("status IN ('active','stopped')", name="chk_campaign_status"),
    )

    # частичный уникальный индекс: одна активная кампания на пользователя
    op.create_index(
        "uq_campaigns_user_active",
        "campaigns",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # 2) ссылки из заявок на кампанию (NULLable, с FK)
    op.add_column("applications", sa.Column("campaign_id", sa.BigInteger, nullable=True))
    op.create_foreign_key(
        "applications_campaign_fk",
        "applications",
        "campaigns",
        ["campaign_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_applications_campaign_id",
        "applications",
        ["campaign_id"],
        unique=False,
    )

    op.add_column("applications_queue", sa.Column("campaign_id", sa.BigInteger, nullable=True))
    op.create_foreign_key(
        "applications_queue_campaign_fk",
        "applications_queue",
        "campaigns",
        ["campaign_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_applications_queue_campaign_id",
        "applications_queue",
        ["campaign_id"],
        unique=False,
    )

    op.add_column("applications_log", sa.Column("campaign_id", sa.BigInteger, nullable=True))
    op.create_foreign_key(
        "applications_log_campaign_fk",
        "applications_log",
        "campaigns",
        ["campaign_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_applications_log_campaign_id",
        "applications_log",
        ["campaign_id"],
        unique=False,
    )

def downgrade():
    # удалить индексы и FK на campaign_id
    op.drop_index("idx_applications_log_campaign_id", table_name="applications_log")
    op.drop_constraint("applications_log_campaign_fk", "applications_log", type_="foreignkey")
    op.drop_column("applications_log", "campaign_id")

    op.drop_index("idx_applications_queue_campaign_id", table_name="applications_queue")
    op.drop_constraint("applications_queue_campaign_fk", "applications_queue", type_="foreignkey")
    op.drop_column("applications_queue", "campaign_id")

    op.drop_index("idx_applications_campaign_id", table_name="applications")
    op.drop_constraint("applications_campaign_fk", "applications", type_="foreignkey")
    op.drop_column("applications", "campaign_id")

    # удалить partial unique индекс и таблицу campaigns
    op.drop_index("uq_campaigns_user_active", table_name="campaigns")
    op.drop_table("campaigns")
