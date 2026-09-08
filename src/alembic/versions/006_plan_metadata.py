"""opaque plan metadata

Lets a product describe what a plan grants without this service learning its
vocabulary. Stored as JSON and handed back untouched; nothing here reads it.

Revision ID: 006
Revises: 005
"""

from alembic import op
import sqlalchemy as sa


revision = "006"
down_revision = "005"


def upgrade():
    op.add_column(
        "plans",
        sa.Column(
            "metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade():
    op.drop_column("plans", "metadata")
