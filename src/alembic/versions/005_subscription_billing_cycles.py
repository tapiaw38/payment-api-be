"""subscription billing cycles and verified webhook events

Revision ID: 005
Revises: 004
"""

from alembic import op
import sqlalchemy as sa


revision = "005"
down_revision = "004"


def upgrade():
    op.alter_column("plans", "amount", type_=sa.Numeric(14, 2), existing_type=sa.Float())
    op.alter_column("payments", "amount", type_=sa.Numeric(14, 2), existing_type=sa.Float())
    op.add_column("subscriptions", sa.Column("next_payment_at", sa.DateTime(), nullable=True))

    op.create_table(
        "billing_cycles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("subscriptions.id"), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("minimum_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="ARS"),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("gateway_authorized_payment_id", sa.String(255), nullable=True, unique=True),
        sa.Column("gateway_payment_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("subscription_id", "period_start", name="uq_billing_cycle_period"),
    )
    op.create_index("ix_billing_cycles_subscription_id", "billing_cycles", ["subscription_id"])
    op.create_index("ix_billing_cycles_status", "billing_cycles", ["status"])
    op.create_index("ix_billing_cycles_gateway_payment_id", "billing_cycles", ["gateway_payment_id"])

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("gateway", sa.String(50), nullable=False),
        sa.Column("gateway_event_id", sa.String(255), nullable=False, unique=True),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(255), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="received"),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_webhook_events_gateway_event_id", "webhook_events", ["gateway_event_id"])
    op.create_index("ix_webhook_events_status", "webhook_events", ["status"])


def downgrade():
    op.drop_table("webhook_events")
    op.drop_table("billing_cycles")
    op.drop_column("subscriptions", "next_payment_at")
    op.alter_column("payments", "amount", type_=sa.Float(), existing_type=sa.Numeric(14, 2))
    op.alter_column("plans", "amount", type_=sa.Float(), existing_type=sa.Numeric(14, 2))
