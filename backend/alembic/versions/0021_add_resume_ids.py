# alembic/versions/0021_add_resume_ids.py
from alembic import op
import sqlalchemy as sa

revision = "0021_add_resume_ids"
down_revision = "0020_auto_responses"
branch_labels = None
depends_on = None


def upgrade():
    # 1) Очередь откликов, из которой читает диспатчер
    op.create_table(
        "applications_queue",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("vacancy_id", sa.BigInteger(), nullable=False),
        sa.Column("resume_id", sa.Text(), nullable=False),      
        sa.Column("cover_letter", sa.Text(), nullable=True),
        sa.Column("origin", sa.Text(), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_applications_queue_user", "applications_queue", ["user_id"])

    op.add_column("auto_responses", sa.Column("resume_id", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("auto_responses", "resume_id")
    op.drop_index("ix_applications_queue_user", table_name="applications_queue")
    op.drop_table("applications_queue")
