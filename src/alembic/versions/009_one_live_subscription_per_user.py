"""Revision ID: 009
Revises: 008
"""

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None

LIVE_STATUSES = ("pending", "authorized", "active", "paused")


def upgrade() -> None:
    op.create_index(
        "uq_subscriptions_live_per_user",
        "subscriptions",
        ["tenant", "user_id"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({', '.join(repr(s) for s in LIVE_STATUSES)})"),
    )


def downgrade() -> None:
    op.drop_index("uq_subscriptions_live_per_user", table_name="subscriptions")
