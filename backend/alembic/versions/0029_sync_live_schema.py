# alembic revision: sync live schema to migrations
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = '0029_sync_live_schema'
down_revision = '0028_ref_percents_in_tariffs'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1) applications.answers
    op.execute("""
        ALTER TABLE public.applications
        ADD COLUMN IF NOT EXISTS answers jsonb NOT NULL DEFAULT '[]'::jsonb;
    """)
    op.execute("ALTER TABLE public.applications ADD COLUMN IF NOT EXISTS cover_letter text;")
    op.execute("ALTER TABLE public.applications ADD COLUMN IF NOT EXISTS sent_at timestamptz;")
    op.execute("ALTER TABLE public.applications ADD COLUMN IF NOT EXISTS error text;")

    op.execute("CREATE INDEX IF NOT EXISTS idx_apps_user_resume ON public.applications(user_id, resume_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_apps_status ON public.applications(status);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_apps_created_at ON public.applications(created_at);")

    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM information_schema.columns 
                 WHERE table_name='applications' AND column_name='source') THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_apps_source_user_resume ON public.applications(source, user_id, resume_id)';
      END IF;

      IF EXISTS (SELECT 1 FROM information_schema.columns 
                 WHERE table_name='applications' AND column_name='is_from_bot') THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_apps_isfrom_user_resume ON public.applications(is_from_bot, user_id, resume_id)';
      END IF;
    END$$;
    """)

    # 2) saved_requests.resume
    op.execute("ALTER TABLE public.saved_requests ADD COLUMN IF NOT EXISTS resume text;")

    # 3) users: дополнительные поля
    op.execute("""
        ALTER TABLE public.users
        ADD COLUMN IF NOT EXISTS email varchar(255),
        ADD COLUMN IF NOT EXISTS last_seen timestamptz,
        ADD COLUMN IF NOT EXISTS hh_account_id varchar(64),
        ADD COLUMN IF NOT EXISTS hh_account_name varchar(255);
    """)

    # 4) resumes (таблица)
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM information_schema.tables 
                     WHERE table_schema='public' AND table_name='resumes') THEN
        CREATE TABLE public.resumes (
          id         BIGSERIAL PRIMARY KEY,
          user_id    BIGINT NOT NULL,
          resume_id  VARCHAR(64) NOT NULL,
          title      TEXT,
          area       TEXT,
          updated_at TIMESTAMPTZ,
          visible    BOOLEAN DEFAULT TRUE
        );
        ALTER TABLE public.resumes 
          ADD CONSTRAINT resumes_resume_id_key UNIQUE (resume_id);
        ALTER TABLE public.resumes 
          ADD CONSTRAINT resumes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;
      END IF;
    END$$;
    """)

    # 5) auto_runs: новые счетчики + surrogate PK id + уникальность (auto_id, d)
    op.execute("ALTER TABLE public.auto_runs ADD COLUMN IF NOT EXISTS taken   integer DEFAULT 0 NOT NULL;")
    op.execute("ALTER TABLE public.auto_runs ADD COLUMN IF NOT EXISTS sent    integer DEFAULT 0 NOT NULL;")
    op.execute("ALTER TABLE public.auto_runs ADD COLUMN IF NOT EXISTS retried integer DEFAULT 0 NOT NULL;")
    op.execute("ALTER TABLE public.auto_runs ADD COLUMN IF NOT EXISTS failed  integer DEFAULT 0 NOT NULL;")
    op.execute("ALTER TABLE public.auto_runs ADD COLUMN IF NOT EXISTS skipped integer DEFAULT 0 NOT NULL;")

    # surrogate PK id
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT 1 
                     FROM information_schema.columns 
                     WHERE table_name='auto_runs' AND column_name='id') THEN
        ALTER TABLE public.auto_runs ADD COLUMN id BIGSERIAL;
        -- если PK ещё не задан
        IF NOT EXISTS (
          SELECT 1 FROM pg_constraint 
          WHERE conrelid = 'public.auto_runs'::regclass AND contype='p'
        ) THEN
          ALTER TABLE public.auto_runs ADD PRIMARY KEY (id);
        END IF;
      END IF;
    END$$;
    """)

    # уникальность (auto_id, d)
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='uq_auto_runs_auto_day') THEN
        ALTER TABLE public.auto_runs ADD CONSTRAINT uq_auto_runs_auto_day UNIQUE (auto_id, d);
      END IF;
    END$$;
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_applications_created_at ON public.applications(created_at)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_app_user_vac ON public.applications(user_id, vacancy_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_email ON public.users(email)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_last_seen ON public.users(last_seen)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_tg_id ON public.users(tg_id)")

    # Валидируем FK как на expected
    op.execute("ALTER TABLE public.users VALIDATE CONSTRAINT fk_users_referred_by")

def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='uq_auto_runs_auto_day') THEN ALTER TABLE public.auto_runs DROP CONSTRAINT uq_auto_runs_auto_day; END IF; END$$;")

    # users extras
    op.execute("ALTER TABLE public.users DROP COLUMN IF EXISTS hh_account_name;")
    op.execute("ALTER TABLE public.users DROP COLUMN IF EXISTS hh_account_id;")
    op.execute("ALTER TABLE public.users DROP COLUMN IF EXISTS last_seen;")
    op.execute("ALTER TABLE public.users DROP COLUMN IF EXISTS email;")

    # saved_requests.resume
    op.execute("ALTER TABLE public.saved_requests DROP COLUMN IF EXISTS resume;")

    # applications.answers
    op.execute("ALTER TABLE public.applications DROP COLUMN IF EXISTS answers;")

    # resumes 
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='resumes') THEN
        DROP TABLE public.resumes;
      END IF;
    END$$;
    """)
