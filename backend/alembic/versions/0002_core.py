# hh/backend/alembic/versions/0002_core.py
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text

# Идентификаторы Alembic
revision = "0002_core"
down_revision = "0001_init"
branch_labels = None
depends_on = None

def _table_exists(conn, name: str) -> bool:
    return sa.inspect(conn).has_table(name)

def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # --- USERS ---
    if not _table_exists(bind, "users"):
        op.create_table(
            "users",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("tg_id", sa.BigInteger, nullable=False, unique=True),
            sa.Column("username", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
    # --- HH_TOKENS ---
    if not _table_exists(bind, "hh_tokens"):
        op.create_table(
            "hh_tokens",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("access_token", sa.Text, nullable=False),
            sa.Column("refresh_token", sa.Text, nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("scope", sa.String(512), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )

    # --- COVER_LETTERS ---
    if not _table_exists(bind, "cover_letters"):
        op.create_table(
            "cover_letters",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("body", sa.Text, nullable=False),
            sa.Column("is_default", sa.Boolean, server_default=sa.text("false"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_cover_letters_user", "cover_letters", ["user_id"])

    # --- TARIFFS ---
    if not _table_exists(bind, "tariffs"):
        op.create_table(
            "tariffs",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("code", sa.String(64), nullable=False, unique=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("price_cents", sa.Integer, nullable=False),
            sa.Column("period_days", sa.Integer, nullable=False),
            sa.Column("limits", postgresql.JSONB, nullable=True),
            sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        )

    # --- SUBSCRIPTIONS ---
    if not _table_exists(bind, "subscriptions"):
        op.create_table(
            "subscriptions",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("tariff_id", sa.BigInteger, sa.ForeignKey("tariffs.id"), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("source", sa.String(32), nullable=True),
        )
        op.create_index("ix_subscriptions_user_expires", "subscriptions", ["user_id", "expires_at"])

    # --- PAYMENTS ---
    if not _table_exists(bind, "payments"):
        op.create_table(
            "payments",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("provider", sa.String(32), nullable=False),
            sa.Column("provider_id", sa.String(128), nullable=False, unique=True),
            sa.Column("tariff_id", sa.BigInteger, sa.ForeignKey("tariffs.id")),
            sa.Column("amount_cents", sa.Integer, nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("raw", postgresql.JSONB, nullable=True),
        )

    # --- APPLICATIONS ---
    if not _table_exists(bind, "applications"):
        op.create_table(
            "applications",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("vacancy_id", sa.BigInteger, nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="sent"),
            sa.Column("cover_letter_id", sa.BigInteger, sa.ForeignKey("cover_letters.id")),
            sa.Column("source", sa.String(32), nullable=True),
            sa.Column("meta", postgresql.JSONB, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        # Индексы
        op.execute("CREATE INDEX IF NOT EXISTS ix_applications_user_created ON applications (user_id, created_at)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_applications_vacancy ON applications (vacancy_id)")
        # Антидубли
        op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_applications_user_vacancy'
            ) THEN
                ALTER TABLE applications
                ADD CONSTRAINT uq_applications_user_vacancy UNIQUE (user_id, vacancy_id);
            END IF;
        END$$;
        """)
    else:
        # Таблица уже есть — гарантируем индексы/уникальность
        op.execute("CREATE INDEX IF NOT EXISTS ix_applications_user_created ON applications (user_id, created_at)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_applications_vacancy ON applications (vacancy_id)")
        op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_applications_user_vacancy'
            ) THEN
                ALTER TABLE applications
                ADD CONSTRAINT uq_applications_user_vacancy UNIQUE (user_id, vacancy_id);
            END IF;
        END$$;
        """)

    # --- REFERRALS ---
    if not _table_exists(bind, "referrals"):
        op.create_table(
            "referrals",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("parent_user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("level", sa.SmallInteger, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_referrals_parent_level", "referrals", ["parent_user_id", "level"])
        op.create_index("ix_referrals_user", "referrals", ["user_id"])

    # --- REFERRAL_BALANCES ---
    if not _table_exists(bind, "referral_balances"):
        op.create_table(
            "referral_balances",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("balance_cents", sa.Integer, nullable=False, server_default="0"),
        )

    # --- REFERRAL_TRANSACTIONS ---
    if not _table_exists(bind, "referral_transactions"):
        op.create_table(
            "referral_transactions",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("amount_cents", sa.Integer, nullable=False),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("related_user_id", sa.BigInteger, sa.ForeignKey("users.id")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_ref_trx_user_created", "referral_transactions", ["user_id", "created_at"])

    # --- NEWSLETTERS ---
    if not _table_exists(bind, "newsletters"):
        op.create_table(
            "newsletters",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("segment", sa.String(64), nullable=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("body", sa.Text, nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        )

    # --- ADMIN_LOGS ---
    if not _table_exists(bind, "admin_logs"):
        op.create_table(
            "admin_logs",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("admin_id", sa.BigInteger, nullable=True),
            sa.Column("action", sa.String(64), nullable=False),
            sa.Column("object_type", sa.String(64), nullable=True),
            sa.Column("object_id", sa.BigInteger, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("meta", postgresql.JSONB, nullable=True),
        )

    # --- BOT_LOGS ---
    if not _table_exists(bind, "bot_logs"):
        op.create_table(
            "bot_logs",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id")),
            sa.Column("level", sa.String(16), nullable=False),
            sa.Column("message", sa.Text, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("meta", postgresql.JSONB, nullable=True),
        )
        op.create_index("ix_bot_logs_created", "bot_logs", ["created_at"])


def downgrade():
    bind = op.get_bind()

    for idx_sql in [
        "DROP INDEX IF EXISTS ix_bot_logs_created",
        "DROP INDEX IF EXISTS ix_ref_trx_user_created",
        "DROP INDEX IF EXISTS ix_referrals_parent_level",
        "DROP INDEX IF EXISTS ix_referrals_user",
        "DROP INDEX IF EXISTS ix_applications_user_created",
        "DROP INDEX IF EXISTS ix_applications_vacancy",
        "DROP INDEX IF EXISTS ix_cover_letters_user",
        "DROP INDEX IF EXISTS ix_subscriptions_user_expires",
    ]:
        op.execute(idx_sql)

    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname='uq_applications_user_vacancy'
        ) THEN
            ALTER TABLE applications DROP CONSTRAINT uq_applications_user_vacancy;
        END IF;
    END$$;
    """)

    for table in [
        "bot_logs",
        "admin_logs",
        "newsletters",
        "referral_transactions",
        "referral_balances",
        "referrals",
        "applications",
        "payments",
        "subscriptions",
        "tariffs",
        "cover_letters",
        "hh_tokens",
    ]:
        if _table_exists(bind, table):
            op.drop_table(table)
