
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '10e238349d4d'
down_revision: Union[str, Sequence[str], None] = '88e3aa483eb2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.drop_constraint("chk_app_status", "applications", type_="check")
    op.create_check_constraint(
        "chk_app_status",
        "applications",
        "status IN ('queued','sent','error','retry','invited','declined','viewed')",
    )

def downgrade():
    op.drop_constraint("chk_app_status", "applications", type_="check")
    op.create_check_constraint(
        "chk_app_status",
        "applications",
        "status IN ('queued','sent','error','retry')",
    )