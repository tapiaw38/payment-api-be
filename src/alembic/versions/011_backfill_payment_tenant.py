"""give existing payments and saved cards the tenant that owns them

Revision ID: 011
Revises: 010
"""

from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


BACKFILL = """
UPDATE {table} AS t
SET tenant = s.tenant
FROM (
    SELECT DISTINCT ON (user_id) user_id, tenant
    FROM subscriptions
    ORDER BY user_id, created_at DESC
) AS s
WHERE t.user_id = s.user_id
  AND t.tenant = 'default'
  AND s.tenant <> 'default'
"""

SQLITE_BACKFILL = """
UPDATE {table}
SET tenant = (
    SELECT s.tenant FROM subscriptions s
    WHERE s.user_id = {table}.user_id
    ORDER BY s.created_at DESC LIMIT 1
)
WHERE tenant = 'default'
  AND EXISTS (
      SELECT 1 FROM subscriptions s
      WHERE s.user_id = {table}.user_id AND s.tenant <> 'default'
  )
"""


def upgrade() -> None:
    statement = SQLITE_BACKFILL if op.get_bind().dialect.name == "sqlite" else BACKFILL
    for table in ("payments", "payment_methods"):
        op.execute(sa.text(statement.format(table=table)))


def downgrade() -> None:
    pass
