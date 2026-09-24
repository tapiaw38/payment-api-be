"""Record which account was asked to pay.

Mercado Pago demands a payer_email up front and then refuses the checkout
unless the person who logs in owns that address — a failure that happens on
its page and never reaches our logs. It does not give the address back either,
so without storing it we cannot tell an agreement opened for one account from
one opened for another, and "try again with my real Mercado Pago email" would
have silently returned the payer to the agreement that already rejected them.
"""

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("payer_email", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("subscriptions", "payer_email")
