from alembic import op
import sqlalchemy as sa

revision = "0022_add_resume_id"
down_revision = "0021_add_resume_ids"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("applications", sa.Column("resume_id", sa.Text(), nullable=True))

def downgrade():
    op.drop_column("applications", "resume_id")
