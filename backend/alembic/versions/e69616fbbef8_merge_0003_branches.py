
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e69616fbbef8'
down_revision: Union[str, Sequence[str], None] = ('0003_hh_tokens_token_type', '0003_tariffs_nemiling')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
