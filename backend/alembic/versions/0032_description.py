from alembic import op

revision = "0032_description"
down_revision = "0031_transactions"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
    CREATE OR REPLACE VIEW public.user_operations AS
    -- manual / transactions
    SELECT
      'manual:' || t.id::text          AS op_uid,
      'manual'                         AS source,
      t.id                             AS source_id,
      t.user_id,
      t.operation_type,
      t.status,
      t.amount_cents,
      t.currency,
      COALESCE(t.description, 'Manual operation') AS description,
      t.created_at
    FROM public.transactions t

    UNION ALL

    -- payments
    SELECT
      'payment:' || p.id::text         AS op_uid,
      'payment'                        AS source,
      p.id                             AS source_id,
      p.user_id,
      (CASE WHEN p.amount_cents >= 0 THEN 'PAYMENT' ELSE 'REFUND' END)::operation_type AS operation_type,
      (
        CASE lower(p.status)
          WHEN 'success'         THEN 'SUCCESS'
          WHEN 'paid'            THEN 'SUCCESS'
          WHEN 'succeeded'       THEN 'SUCCESS'
          WHEN 'pending'         THEN 'PENDING'
          WHEN 'requires_action' THEN 'PENDING'
          WHEN 'failed'          THEN 'FAILED'
          WHEN 'canceled'        THEN 'FAILED'
          WHEN 'cancelled'       THEN 'FAILED'
          ELSE 'PENDING'
        END
      )::operation_status             AS status,
      p.amount_cents,
      NULL::varchar                   AS currency,
      COALESCE(p.description, p.raw->>'description', 'Payment') AS description,
      p.created_at
    FROM public.payments p

    UNION ALL

    -- referrals
    SELECT
      'referral:' || r.id::text       AS op_uid,
      'referral'                      AS source,
      r.id                            AS source_id,
      r.user_id,
      (CASE WHEN r.amount_cents >= 0 THEN 'BONUS' ELSE 'CHARGE' END)::operation_type AS operation_type,
      'SUCCESS'::operation_status     AS status,
      r.amount_cents,
      NULL::varchar                   AS currency,
      COALESCE(NULLIF(r.kind, ''), 'Referral') AS description,
      r.created_at
    FROM public.referral_transactions r;
    """)
    
def downgrade():
    op.execute("""
    CREATE OR REPLACE VIEW public.user_operations AS
    SELECT
      'manual:' || t.id::text AS op_uid,
      'manual'                AS source,
      t.id                    AS source_id,
      t.user_id,
      t.operation_type,
      t.status,
      t.amount_cents,
      t.currency,
      COALESCE(t.description, 'Manual operation') AS description,
      t.created_at
    FROM public.transactions t

    UNION ALL

    SELECT
      'payment:' || p.id::text AS op_uid,
      'payment'                AS source,
      p.id                     AS source_id,
      p.user_id,
      (CASE WHEN p.amount_cents >= 0 THEN 'PAYMENT' ELSE 'REFUND' END)::operation_type AS operation_type,
      (
        CASE lower(p.status)
          WHEN 'success' THEN 'SUCCESS'
          WHEN 'paid'    THEN 'SUCCESS'
          WHEN 'succeeded' THEN 'SUCCESS'
          WHEN 'pending' THEN 'PENDING'
          WHEN 'requires_action' THEN 'PENDING'
          WHEN 'failed'  THEN 'FAILED'
          WHEN 'canceled' THEN 'FAILED'
          WHEN 'cancelled' THEN 'FAILED'
          ELSE 'PENDING'
        END
      )::operation_status AS status,
      p.amount_cents,
      NULL::varchar       AS currency,
      'Payment'           AS description,
      p.created_at
    FROM public.payments p

    UNION ALL

    SELECT
      'referral:' || r.id::text AS op_uid,
      'referral'                AS source,
      r.id                      AS source_id,
      r.user_id,
      (CASE WHEN r.amount_cents >= 0 THEN 'BONUS' ELSE 'CHARGE' END)::operation_type AS operation_type,
      'SUCCESS'::operation_status AS status,
      r.amount_cents,
      NULL::varchar             AS currency,
      COALESCE(NULLIF(r.kind, ''), 'Referral') AS description,
      r.created_at
    FROM public.referral_transactions r;
    """)
