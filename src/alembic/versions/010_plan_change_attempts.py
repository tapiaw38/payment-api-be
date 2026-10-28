from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_change_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant", sa.String(64), nullable=False),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("subscriptions.id"), nullable=False),
        sa.Column("source_plan_id", sa.Integer(), sa.ForeignKey("plans.id"), nullable=False),
        sa.Column("target_plan_id", sa.Integer(), sa.ForeignKey("plans.id"), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=True),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("target_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=True),
        sa.Column("external_reference", sa.String(255), nullable=True),
        sa.Column("gateway_payment_id", sa.String(255), nullable=True),
        sa.Column("gateway_status", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_plan_change_attempts_tenant", "plan_change_attempts", ["tenant"])
    op.create_index("ix_plan_change_attempts_subscription_id", "plan_change_attempts", ["subscription_id"])
    op.create_index("uq_plan_change_attempts_idempotency_key", "plan_change_attempts", ["idempotency_key"], unique=True)
    op.create_index("uq_plan_change_attempts_external_reference", "plan_change_attempts", ["external_reference"], unique=True)
    op.create_index(
        "uq_open_plan_change_attempt",
        "plan_change_attempts",
        ["tenant", "subscription_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('prepared', 'sending', 'unknown', 'charged')"),
    )


def downgrade() -> None:
    op.drop_index("uq_open_plan_change_attempt", table_name="plan_change_attempts")
    op.drop_index("uq_plan_change_attempts_external_reference", table_name="plan_change_attempts")
    op.drop_index("uq_plan_change_attempts_idempotency_key", table_name="plan_change_attempts")
    op.drop_index("ix_plan_change_attempts_subscription_id", table_name="plan_change_attempts")
    op.drop_index("ix_plan_change_attempts_tenant", table_name="plan_change_attempts")
    op.drop_table("plan_change_attempts")
