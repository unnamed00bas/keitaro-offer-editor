"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-11-17 22:12:14.316603
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('campaigns',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kt_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('alias', sa.String(length=255), nullable=True),
    sa.Column('geo', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_campaigns_kt_id'), ['kt_id'], unique=True)

    op.create_table('streams',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kt_id', sa.Integer(), nullable=False),
    sa.Column('campaign_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('kt_type', sa.String(length=32), nullable=True),
    sa.Column('kt_schema', sa.String(length=32), nullable=True),
    sa.Column('has_offers', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('streams', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_streams_kt_id'), ['kt_id'], unique=True)

    op.create_table('stream_offers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('stream_id', sa.Integer(), nullable=False),
    sa.Column('offer_id', sa.Integer(), nullable=False),
    sa.Column('offer_name', sa.String(length=512), nullable=False),
    sa.Column('share', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('pinned', sa.Boolean(), nullable=False),
    sa.Column('pub_share', sa.Integer(), nullable=True),
    sa.Column('pub_status', sa.String(length=16), nullable=True),
    sa.Column('pub_pinned', sa.Boolean(), nullable=False),
    sa.Column('removed_in_kt', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['stream_id'], ['streams.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stream_id', 'offer_id', name='uq_stream_offer')
    )


def downgrade():
    op.drop_table('stream_offers')
    with op.batch_alter_table('streams', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_streams_kt_id'))

    op.drop_table('streams')
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_campaigns_kt_id'))

    op.drop_table('campaigns')
