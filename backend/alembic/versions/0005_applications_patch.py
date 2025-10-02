"""patch applications: add cover_letter, ensure unique + indexes"""

from alembic import op
import sqlalchemy as sa

revision = "0005_applications_patch"
down_revision = "0004_applications"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE applications
        ADD COLUMN IF NOT EXISTS cover_letter TEXT
    """)

    op.execute("""
        WITH d AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY user_id, vacancy_id
                       ORDER BY id
                   ) AS rn
            FROM applications
        )
        DELETE FROM applications a
        USING d
        WHERE a.id = d.id AND d.rn > 1
    """)

    # 3) Уникальность пары (user_id, vacancy_id) — идемпотентно
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_applications_user_vacancy
        ON applications (user_id, vacancy_id)
    """)

def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_applications_user_vacancy")
