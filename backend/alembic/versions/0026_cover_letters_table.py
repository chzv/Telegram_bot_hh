# alembic/versions/0026_cover_letters_table.py
from alembic import op
import sqlalchemy as sa

revision = "0026_cover_letters_table"
down_revision = "0025_query_params"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "cover_letters" not in insp.get_table_names(schema="public"):
        op.create_table(
            "cover_letters",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("user_id", sa.BigInteger, nullable=False),
            sa.Column("title", sa.Text, nullable=False),
            sa.Column("body", sa.Text, nullable=False),
            sa.Column("created_at", sa.TIMESTAMP(timezone=False), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.TIMESTAMP(timezone=False), nullable=False, server_default=sa.text("now()")),
        )

def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "cover_letters" in insp.get_table_names(schema="public"):
        op.drop_table("cover_letters")