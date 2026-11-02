"""give existing payments and saved cards the tenant this deployment serves

Revision ID: 011
Revises: 010
"""

from alembic import op
import sqlalchemy as sa

from config.settings import settings

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def target_tenant() -> str | None:
    tenants = set(settings.tenant_by_api_key.values())
    if len(tenants) != 1:
        return None
    tenant = tenants.pop()
    return None if tenant == "default" else tenant


def upgrade() -> None:
    tenant = target_tenant()
    if not tenant:
        return
    for table in ("payments", "payment_methods"):
        op.execute(
            sa.text(f"UPDATE {table} SET tenant = :tenant WHERE tenant = 'default'").bindparams(
                tenant=tenant
            )
        )


def downgrade() -> None:
    pass
