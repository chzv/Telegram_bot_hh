from alembic import op

revision = "0033_seed_tariffs"
down_revision = "0032_description"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
        INSERT INTO tariffs (code, title, price_cents, period_days, is_active)
        VALUES 
          ('week',  'Подписка 7 дней', 69000, 7,  true),
          ('month', 'Подписка 30 дней',190000,30,  true)
        ON CONFLICT (code) DO UPDATE
        SET price_cents = EXCLUDED.price_cents,
            period_days = EXCLUDED.period_days,
            is_active = EXCLUDED.is_active
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tariffs_code ON tariffs(code)")

def downgrade():
    op.execute("UPDATE tariffs SET is_active=false WHERE code IN ('week','month')")