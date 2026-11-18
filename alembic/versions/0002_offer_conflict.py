"""stream_offers.conflict: пометка расхождения черновика с Keitaro после Fetch

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("stream_offers") as batch_op:
        batch_op.add_column(sa.Column("conflict", sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table("stream_offers") as batch_op:
        batch_op.drop_column("conflict")
