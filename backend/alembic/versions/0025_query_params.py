from alembic import op
import sqlalchemy as sa

# alembic ids
revision = "0025_query_params"
down_revision = "0024_name_to_auto"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column(
        "saved_requests",
        sa.Column("query_params", sa.Text(), nullable=True),
    )

    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public'
              AND table_name='saved_requests'
              AND column_name='url'
        ) THEN
            UPDATE saved_requests
               SET query_params = NULLIF(split_part(url, '?', 2), '')
             WHERE query_params IS NULL;
        END IF;
    END$$;
    """)

def downgrade():
    with op.batch_alter_table("saved_requests") as b:
        b.drop_column("query_params")
