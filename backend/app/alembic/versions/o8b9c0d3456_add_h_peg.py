"""add H-PEG value definition

Revision ID: o8b9c0d3456
Revises: n7a89b0c2345
Create Date: 2026-05-20 10:30:00.000000

"""
from alembic import op


revision = 'o8b9c0d3456'
down_revision = 'n7a89b0c2345'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO value_definitions
            (key, label_de, label_en, category, source_type, data_type, unit, sort_order)
        VALUES
            ('h_peg', 'H-PEG', 'H-PEG', 'HOHN_RETURN', 'CALCULATED', 'NUMERIC', NULL, 72)
        ON CONFLICT (key) DO UPDATE SET
            label_de = EXCLUDED.label_de,
            label_en = EXCLUDED.label_en,
            category = EXCLUDED.category,
            source_type = EXCLUDED.source_type,
            data_type = EXCLUDED.data_type,
            unit = EXCLUDED.unit,
            sort_order = EXCLUDED.sort_order
    """)


def downgrade() -> None:
    op.execute("DELETE FROM value_definitions WHERE key = 'h_peg'")
