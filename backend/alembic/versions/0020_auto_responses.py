# alembic/versions/0020_auto_responses.py
from alembic import op
import sqlalchemy as sa

revision = "0020_auto_responses"
down_revision = "0019_add_saved_requests_table"

def upgrade():
    op.create_table(
        "auto_responses",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("saved_request_id", sa.BigInteger, sa.ForeignKey("saved_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("daily_limit", sa.Integer, nullable=False, server_default="200"),
        sa.Column("run_at", sa.Time, nullable=False, server_default=sa.text("'09:00'")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("user_id", "saved_request_id", name="uq_auto_user_request"),
    )
    # отметки «уже запускали сегодня»
    op.create_table(
        "auto_runs",
        sa.Column("auto_id", sa.BigInteger, sa.ForeignKey("auto_responses.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("d", sa.Date, primary_key=True),
        sa.Column("queued", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

def downgrade():
    op.drop_table("auto_runs")
    op.drop_table("auto_responses")
