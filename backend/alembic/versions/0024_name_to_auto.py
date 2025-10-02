from alembic import op
import sqlalchemy as sa

revision = '0024_name_to_auto'                     
down_revision = '0023_add_updated_at'   
branch_labels = None
depends_on = None

def upgrade():
    op.add_column(
        'auto_responses',
        sa.Column('name', sa.Text(), nullable=False, server_default='Правило')
    )
    op.execute("UPDATE auto_responses SET name = 'Правило #' || id::text")
    op.alter_column('auto_responses', 'name', server_default=None)

def downgrade():
    op.drop_column('auto_responses', 'name')
