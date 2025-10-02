"""merge heads

Revision ID: 57531e82dc3e
Revises: 0006_applications_kind_fix, 0007_hh_tokens_unique_user, 0009_applications_retry_window
Create Date: 2025-08-15 19:43:28.894481

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '57531e82dc3e'
down_revision: Union[str, Sequence[str], None] = ('0006_applications_kind_fix', '0007_hh_tokens_unique_user', '0009_applications_retry_window')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
