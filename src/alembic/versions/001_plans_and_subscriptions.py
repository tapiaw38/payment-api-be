"""plans and subscriptions tables

Revision ID: 001
Revises:
Create Date: 2025-02-10

"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None


def upgrade():
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(3), server_default="ARS"),
        sa.Column("interval", sa.String(20), nullable=False),
        sa.Column("interval_count", sa.Integer(), server_default="1"),
        sa.Column("gateway", sa.String(50), nullable=False, server_default="mercadopago"),
        sa.Column("gateway_plan_id", sa.String(255), nullable=True),
        sa.Column("active", sa.Integer(), server_default="1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_plans_gateway_plan_id", "plans", ["gateway_plan_id"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plans.id"), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("gateway", sa.String(50), nullable=False, server_default="mercadopago"),
        sa.Column("gateway_subscription_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("current_period_start", sa.DateTime(), nullable=True),
        sa.Column("current_period_end", sa.DateTime(), nullable=True),
        sa.Column("cancel_at_period_end", sa.Integer(), server_default="0"),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    op.create_index("ix_subscriptions_gateway_subscription_id", "subscriptions", ["gateway_subscription_id"])
    op.create_index("ix_subscriptions_status", "subscriptions", ["status"])


def downgrade():
    op.drop_table("subscriptions")
    op.drop_table("plans")
