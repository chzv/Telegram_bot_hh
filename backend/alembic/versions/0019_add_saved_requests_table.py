from alembic import op
import sqlalchemy as sa

revision = "0019_add_saved_requests_table"
down_revision = "0018_u_profile_last_seen_at"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'saved_requests',
        sa.Column('id', sa.BigInteger, primary_key=True),
        sa.Column('user_id', sa.BigInteger, nullable=False),
        sa.Column('title', sa.Text, nullable=False),
        sa.Column('query', sa.Text, nullable=False),
        sa.Column('area', sa.Integer),
        sa.Column('employment', sa.ARRAY(sa.Text)),
        sa.Column('schedule', sa.ARRAY(sa.Text)),
        sa.Column('professional_roles', sa.ARRAY(sa.Integer)),
        sa.Column('search_fields', sa.ARRAY(sa.Text)),
        sa.Column('cover_letter', sa.Text),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('idx_saved_requests_user', 'saved_requests', ['user_id'])

    op.create_table(
        'daily_counters',
        sa.Column('user_id', sa.BigInteger, nullable=False),
        sa.Column('d', sa.Date, nullable=False),
        sa.Column('sent', sa.Integer, nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint('user_id', 'd')
    )

def downgrade():
    op.drop_table('daily_counters')
    op.drop_index('idx_saved_requests_user', table_name='saved_requests')
    op.drop_table('saved_requests')