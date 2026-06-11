"""expand source_name to 4096 chars for Konsens-Range + Quartals-Aufschluesselung

Revision ID: p9c0d4e567
Revises: o8b9c0d3456
Create Date: 2026-06-11 09:30:00.000000

"""
from alembic import op


revision = 'p9c0d4e567'
down_revision = 'o8b9c0d3456'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE company_values ALTER COLUMN source_name TYPE VARCHAR(4096)")


def downgrade() -> None:
    op.execute("ALTER TABLE company_values ALTER COLUMN source_name TYPE VARCHAR(2048)")
