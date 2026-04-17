"""add_unique_constraint_company_values

Revision ID: dc287206e3c0
Revises: 0a4be665e23c
Create Date: 2026-04-17 10:34:15.076160

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'dc287206e3c0'
down_revision = '0a4be665e23c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_company_values ON company_values "
        "(company_id, value_key, period_type, COALESCE(period_year, -1), is_forecast)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_company_values")
