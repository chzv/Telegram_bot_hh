from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0031_transactions"
down_revision = "0030_referral_indexes"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'operation_type') THEN
            CREATE TYPE operation_type AS ENUM ('PAYMENT','REFUND','BONUS','CHARGE','ADJUSTMENT');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'operation_status') THEN
            CREATE TYPE operation_status AS ENUM ('PENDING','SUCCESS','FAILED','CANCELLED');
        END IF;
    END$$;
    """)

    op.create_table(
        'transactions',
        sa.Column('id', sa.BigInteger, primary_key=True),
        sa.Column('user_id', sa.BigInteger,
                  sa.ForeignKey('users.id', ondelete='CASCADE'),
                  index=True, nullable=False),

        sa.Column(
            'operation_type',
            postgresql.ENUM('PAYMENT','REFUND','BONUS','CHARGE','ADJUSTMENT',
                            name='operation_type', create_type=False),
            nullable=False
        ),
        sa.Column(
            'status',
            postgresql.ENUM('PENDING','SUCCESS','FAILED','CANCELLED',
                            name='operation_status', create_type=False),
            nullable=False,
            server_default=sa.text("'SUCCESS'::operation_status")
        ),

        sa.Column('amount_cents', sa.Integer, nullable=False),
        sa.Column('currency', sa.String(3), nullable=False, server_default='RUB'),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('source', sa.String(32), nullable=False, server_default='manual'),
        sa.Column('external_id', sa.String(128), nullable=True),
        sa.Column('performed_by', sa.BigInteger, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_transactions_user_created', 'transactions', ['user_id', 'created_at'])

    with op.batch_alter_table('payments') as b:
        b.add_column(sa.Column('description', sa.Text(), nullable=True))

    # VIEW для админки
    op.execute("""
    CREATE OR REPLACE VIEW user_operations AS
    SELECT
        'manual:'||t.id::text          AS op_uid,
        'manual'                        AS source,
        t.id                            AS source_id,
        t.user_id,
        t.operation_type,
        t.status,
        t.amount_cents,
        t.currency,
        COALESCE(t.description,'Manual operation') AS description,
        t.created_at
    FROM transactions t
    UNION ALL
    SELECT
        'payment:'||p.id::text          AS op_uid,
        'payment'                        AS source,
        p.id                             AS source_id,
        p.user_id,
        CASE WHEN p.amount_cents >= 0 THEN 'PAYMENT' ELSE 'REFUND' END::operation_type AS operation_type,
        CASE
            WHEN lower(p.status) IN ('success','paid','succeeded') THEN 'SUCCESS'
            WHEN lower(p.status) IN ('pending','requires_action') THEN 'PENDING'
            WHEN lower(p.status) IN ('failed','canceled','cancelled') THEN 'FAILED'
            ELSE 'PENDING'
        END::operation_status           AS status,
        p.amount_cents,
        'RUB'                            AS currency,
        COALESCE(p.description, p.provider||' '||p.provider_id) AS description,
        p.created_at
    FROM payments p
    UNION ALL
    SELECT
        'referral:'||rt.id::text        AS op_uid,
        'referral'                       AS source,
        rt.id                            AS source_id,
        rt.user_id,
        CASE WHEN rt.amount_cents >= 0 THEN 'BONUS' ELSE 'CHARGE' END::operation_type AS operation_type,
        'SUCCESS'::operation_status      AS status,
        rt.amount_cents,
        'RUB'                            AS currency,
        COALESCE(rt.kind, 'Referral')    AS description,
        rt.created_at
    FROM referral_transactions rt;
    """)

def downgrade():
    op.execute("DROP VIEW IF EXISTS user_operations;")
    op.drop_index('ix_transactions_user_created', table_name='transactions')
    op.drop_table('transactions')
    with op.batch_alter_table('payments') as b:
        b.drop_column('description')
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_type WHERE typname='operation_status') THEN DROP TYPE operation_status; END IF; END$$;")
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_type WHERE typname='operation_type') THEN DROP TYPE operation_type; END IF; END$$;")
