"""add actual_return value definition

Adds the realised total shareholder return (FY-end MCap / FY-start MCap - 1
+ dividend yield) as a CALCULATED value next to hohn_return_detailed in
the HOHN_RETURN category. Idempotent — only inserts when missing.

Revision ID: f8a9b1c23456
Revises: d5f7e9b12345
Create Date: 2026-05-05 19:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f8a9b1c23456'
down_revision = 'd5f7e9b12345'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO value_definitions "
            "(key, label_de, label_en, category, source_type, data_type, unit, sort_order) "
            "VALUES ('actual_return', 'Tatsächliche Rendite (FY)', 'Actual Return (FY)', "
            "CAST('HOHN_RETURN' AS valuecategory), "
            "CAST('CALCULATED' AS sourcetype), "
            "CAST('NUMERIC' AS datatype), "
            "'%', 72) "
            "ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute("DELETE FROM value_definitions WHERE key = 'actual_return'")
