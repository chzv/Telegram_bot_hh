"""applications queue + applications_log (idempotent)"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0004_applications"
down_revision = "e69616fbbef8"
branch_labels = None
depends_on = None


def _table_exists(insp, name: str) -> bool:
    return name in insp.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if not _table_exists(insp, "applications"):
        op.create_table(
            "applications",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column(
                "user_id",
                sa.BigInteger,
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("vacancy_id", sa.BigInteger, nullable=False),
            sa.Column(
                "status",
                sa.String(16),
                nullable=False,
                server_default="queued",  # queued | sent | error | retry
            ),
            sa.Column("cover_letter", sa.Text(), nullable=True),  
            sa.Column(
                "created_at",
                sa.TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),  
        )
        # индексы под воркер и статистику
        op.create_index(
            "ix_applications_user_status", "applications", ["user_id", "status"]
        )
        op.create_index("ix_applications_vacancy", "applications", ["vacancy_id"])
        op.create_index(
            "ix_applications_created_at", "applications", ["created_at"]
        )
        op.create_unique_constraint(
            "uq_applications_user_vacancy",
            "applications",
            ["user_id", "vacancy_id"],
        )

    # 2) applications_log (аудит статусов)
    if not _table_exists(insp, "applications_log"):
        op.create_table(
            "applications_log",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column(
                "application_id",
                sa.BigInteger,
                sa.ForeignKey("applications.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("event", sa.String(32), nullable=False),  # queued|sent|error|retry
            sa.Column("details", sa.Text(), nullable=True),     
            sa.Column(
                "created_at",
                sa.TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index(
            "ix_applications_log_app", "applications_log", ["application_id"]
        )
        op.create_index(
            "ix_applications_log_created_at",
            "applications_log",
            ["created_at"],
        )

    # 3) CHECK для статуса (без падений при повторном запуске)
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'chk_app_status'
          ) THEN
            ALTER TABLE applications
            ADD CONSTRAINT chk_app_status
            CHECK (status IN ('queued','sent','error','retry'));
          END IF;
        END $$;
        """
    )

    # 4) Триггер логирования: INSERT/UPDATE → applications_log
    op.execute(
        """
        CREATE OR REPLACE FUNCTION log_applications_status()
        RETURNS TRIGGER AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            INSERT INTO applications_log(application_id, event, details)
            VALUES (NEW.id, NEW.status, NULL);
            RETURN NEW;
          ELSIF TG_OP = 'UPDATE' THEN
            IF NEW.status IS DISTINCT FROM OLD.status THEN
              INSERT INTO applications_log(application_id, event, details)
              VALUES (NEW.id, NEW.status, NEW.error);
            END IF;
            RETURN NEW;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_applications_log ON applications;
        CREATE TRIGGER trg_applications_log
        AFTER INSERT OR UPDATE ON applications
        FOR EACH ROW EXECUTE FUNCTION log_applications_status();
        """
    )


def downgrade() -> None:
    # триггер и функция
    op.execute("DROP TRIGGER IF EXISTS trg_applications_log ON applications;")
    op.execute("DROP FUNCTION IF EXISTS log_applications_status();")

    # индексы/таблицы (аккуратно, если существуют)
    op.execute("DROP INDEX IF EXISTS ix_applications_log_created_at;")
    op.execute("DROP INDEX IF EXISTS ix_applications_log_app;")
    if _table_exists(inspect(op.get_bind()), "applications_log"):
        op.drop_table("applications_log")

    op.execute("ALTER TABLE applications DROP CONSTRAINT IF EXISTS chk_app_status;")
    op.execute("DROP INDEX IF EXISTS ix_applications_created_at;")
    op.execute("DROP INDEX IF EXISTS ix_applications_vacancy;")
    op.execute("DROP INDEX IF EXISTS ix_applications_user_status;")
    op.execute("ALTER TABLE applications DROP CONSTRAINT IF EXISTS uq_applications_user_vacancy;")
    if _table_exists(inspect(op.get_bind()), "applications"):
        op.drop_table("applications")
