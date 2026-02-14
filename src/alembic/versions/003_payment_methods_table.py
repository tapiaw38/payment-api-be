"""payment_methods table

Revision ID: 003
Revises: 002
Create Date: 2026-02-05

"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"


def upgrade():
    op.create_table(
        "payment_methods",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("gateway", sa.String(50), nullable=False, server_default="mercadopago"),
        sa.Column("card_token_id", sa.String(255), nullable=False),
        sa.Column("last_four_digits", sa.String(4), nullable=False),
        sa.Column("payment_method_id", sa.String(50), nullable=False),
        sa.Column("cardholder_name", sa.String(255), nullable=False),
        sa.Column("expiration_month", sa.String(2), nullable=False),
        sa.Column("expiration_year", sa.String(4), nullable=False),
        sa.Column("is_default", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_payment_methods_user_id", "payment_methods", ["user_id"])


def downgrade():
    op.drop_table("payment_methods")
