from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '88e3aa483eb2'
down_revision: Union[str, Sequence[str], None] = '41aec5f5cf8a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS app_settings (
      key TEXT PRIMARY KEY,
      value JSONB NOT NULL DEFAULT '{}'::jsonb,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """);
    op.execute("""
    INSERT INTO app_settings(key, value)
    VALUES ('free_replies', jsonb_build_object('count', 10))
    ON CONFLICT (key) DO NOTHING;
    """)

def downgrade():
    op.execute("DROP TABLE IF EXISTS app_settings;")
    pass