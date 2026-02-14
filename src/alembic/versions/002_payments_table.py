"""payments table

Revision ID: 002
Revises: 001
Create Date: 2025-02-10

"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"


def upgrade():
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("gateway", sa.String(50), nullable=False, server_default="mercadopago"),
        sa.Column("gateway_payment_id", sa.String(255), nullable=True),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(3), server_default="ARS"),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=True),
        sa.Column("external_reference", sa.String(255), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_payments_gateway_payment_id", "payments", ["gateway_payment_id"])
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_index("ix_payments_external_reference", "payments", ["external_reference"])
    op.create_index("ix_payments_status", "payments", ["status"])


def downgrade():
    op.drop_table("payments")
