"""add mp_customer_id and mp_card_id to payment_methods

Revision ID: 004
Revises: 003
Create Date: 2026-02-11
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"


def upgrade():
    op.add_column("payment_methods", sa.Column("mp_customer_id", sa.String(255), nullable=True))
    op.add_column("payment_methods", sa.Column("mp_card_id", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("payment_methods", "mp_card_id")
    op.drop_column("payment_methods", "mp_customer_id")
