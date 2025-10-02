# backend/alembic/versions/0012_fix_chk_app_status_retry.py
from alembic import op

revision = "0012_fix_chk_app_status_retry"
down_revision = "57531e82dc3e" 
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
        ALTER TABLE applications DROP CONSTRAINT IF EXISTS chk_app_status;
        ALTER TABLE applications
          ADD CONSTRAINT chk_app_status
          CHECK (status IN ('queued','sent','error','retry'));
    """)

def downgrade():
    op.execute("""
        ALTER TABLE applications DROP CONSTRAINT IF EXISTS chk_app_status;
        ALTER TABLE applications
          ADD CONSTRAINT chk_app_status
          CHECK (status IN ('queued','sent','error'));
    """)
