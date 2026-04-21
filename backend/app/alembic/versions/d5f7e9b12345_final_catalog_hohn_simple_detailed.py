"""final_catalog_hohn_simple_detailed

Final catalog: 28 keys in 8 categories (STAMMDATEN, CASH_DEBT,
BUYBACKS_SBC, FCF, NI_GROWTH, DELTA_ND, DIVIDENDS, HOHN_RETURN).

Simple Hohn Return  = FCF Yield + NI Growth - SBC/MCap + dNetDebt/MCap
Detailed Hohn Return = Div Yield + NI Growth + NetBuyback/MCap + dNetDebt/MCap

Enum is replaced with the new category set. Existing rows are wiped.

Revision ID: d5f7e9b12345
Revises: c4e5f60012
Create Date: 2026-04-21 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

from app.values.catalog import SEED_VALUES


revision = 'd5f7e9b12345'
down_revision = 'c4e5f60012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM llm_messages")
    op.execute("DELETE FROM llm_conversations")
    op.execute("DELETE FROM company_values")
    op.execute("DELETE FROM value_definitions")

    op.execute("DROP TYPE IF EXISTS valuecategory_old")
    op.execute("ALTER TYPE valuecategory RENAME TO valuecategory_old")
    op.execute(
        "CREATE TYPE valuecategory AS ENUM ("
        "'STAMMDATEN', 'CASH_DEBT', 'BUYBACKS_SBC', 'FCF', "
        "'NI_GROWTH', 'DELTA_ND', 'DIVIDENDS', 'HOHN_RETURN'"
        ")"
    )
    op.execute(
        "ALTER TABLE value_definitions "
        "ALTER COLUMN category TYPE valuecategory "
        "USING category::text::valuecategory"
    )
    op.execute("DROP TYPE valuecategory_old")

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
