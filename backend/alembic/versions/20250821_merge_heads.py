"""merge heads: users table + utm fields"""

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "20250821_merge_heads"
down_revision = ("20250817_add_utm_fields_to_users", "20250820_add_users_table")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
