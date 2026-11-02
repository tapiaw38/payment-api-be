"""at most one live payment per reference

Revision ID: 012
Revises: 011
"""

from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None

CREATE = """
CREATE UNIQUE INDEX uq_payments_live_per_reference
ON payments (tenant, external_reference)
WHERE external_reference IS NOT NULL
  AND status NOT IN ('rejected', 'cancelled')
"""


def upgrade() -> None:
    op.execute(CREATE)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_payments_live_per_reference")
