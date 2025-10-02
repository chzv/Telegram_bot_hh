"""users table (idempotent / compatible)"""

from alembic import op

revision = "20250820_add_users_table"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
    DO $$
    BEGIN
        -- создать таблицу, если её нет
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema='public' AND table_name='users'
        ) THEN
            CREATE TABLE users (
                id BIGSERIAL PRIMARY KEY,
                tg_id BIGINT NOT NULL,
                username TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        END IF;

        -- переименовать legacy-колонку при наличии
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='users' AND column_name='telegram_id'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='users' AND column_name='tg_id'
        ) THEN
            ALTER TABLE users RENAME COLUMN telegram_id TO tg_id;
        END IF;

        -- гарантировать наличие tg_id
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='users' AND column_name='tg_id'
        ) THEN
            ALTER TABLE users ADD COLUMN tg_id BIGINT NOT NULL;
        END IF;

        -- индекс уникальности на tg_id
        IF NOT EXISTS (
            SELECT 1 FROM pg_class WHERE relname='idx_users_tg_id'
        ) THEN
            CREATE UNIQUE INDEX idx_users_tg_id ON users (tg_id);
        END IF;
    END $$;
    """)

def downgrade():
    pass
