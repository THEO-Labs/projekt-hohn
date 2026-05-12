"""expand source_name to 2048 chars for dual-provider consensus strings

Revision ID: l5e6f7a89012
Revises: k4d5e6f78901
Create Date: 2026-05-12 13:45:00.000000

"""
from alembic import op


revision = 'l5e6f7a89012'
down_revision = 'k4d5e6f78901'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE company_values ALTER COLUMN source_name TYPE VARCHAR(2048)")


def downgrade() -> None:
    op.execute("ALTER TABLE company_values ALTER COLUMN source_name TYPE VARCHAR(512)")
