"""tenant scope on caller-owned tables

`user_id` is an opaque string from each calling product's own auth, so two
products can legitimately send the same one. Without a tenant, a lookup by user
id could answer with another product's row.

Existing rows belong to whoever is already deployed, which is one product per
database, so they backfill to "default" — the tenant a deployment still using
the single PAYMENTS_API_KEY is given.

Revision ID: 007
Revises: 006
"""

from alembic import op
import sqlalchemy as sa


revision = "007"
down_revision = "006"

TABLES = ("plans", "subscriptions", "payments", "payment_methods")


def upgrade():
    for table in TABLES:
        op.add_column(
            table,
            sa.Column(
                "tenant",
                sa.String(64),
                nullable=False,
                server_default="default",
            ),
        )
        op.create_index(f"ix_{table}_tenant", table, ["tenant"])


def downgrade():
    for table in TABLES:
        op.drop_index(f"ix_{table}_tenant", table_name=table)
        op.drop_column(table, "tenant")
