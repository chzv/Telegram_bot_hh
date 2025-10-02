from alembic import op

revision = "0007_hh_tokens_unique_user"
down_revision = "e69616fbbef8"

def upgrade():
    op.create_unique_constraint("uq_hh_tokens_user", "hh_tokens", ["user_id"])

def downgrade():
    op.drop_constraint("uq_hh_tokens_user", "hh_tokens", type_="unique")
