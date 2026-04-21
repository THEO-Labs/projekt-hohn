"""catalog_per_fy_sbc_and_split

Move SBC from STAMMDATEN to per-FY INPUTS, split Net Debt Change into
absolute and percent fields, and add Hohn Return base/full variants.
Cash definition changes to Cash + MktSec ST + MktSec LT (handled in
provider code, catalog label only).

Revision ID: b3d4e5f601
Revises: a2c3d4e5f601
Create Date: 2026-04-21 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

from app.values.catalog import SEED_VALUES


revision = 'b3d4e5f601'
down_revision = 'a2c3d4e5f601'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM llm_messages")
    op.execute("DELETE FROM llm_conversations")
    op.execute("DELETE FROM company_values")
    op.execute("DELETE FROM value_definitions")

    conn = op.get_bind()
    for row in SEED_VALUES:
        conn.execute(
            sa.text(
                "INSERT INTO value_definitions "
                "(key, label_de, label_en, category, source_type, data_type, unit, sort_order) "
                "VALUES (:key, :label_de, :label_en, "
                "CAST(:category AS valuecategory), "
                "CAST(:source_type AS sourcetype), "
                "CAST(:data_type AS datatype), "
                ":unit, :sort_order)"
            ),
            {
                "key": row["key"],
                "label_de": row["label_de"],
                "label_en": row["label_en"],
                "category": row["category"],
                "source_type": row["source_type"],
                "data_type": row["data_type"],
                "unit": row.get("unit"),
                "sort_order": row["sort_order"],
            },
        )


def downgrade() -> None:
    raise NotImplementedError("One-way catalog reset; no downgrade.")
