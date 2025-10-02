"""baseline empty revision (restored)"""

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    pass

def downgrade() -> None:
    pass
