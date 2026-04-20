"""reset_catalog_new_formula

Wipes all company values, chat history, and the old indicator catalog.
Replaces the ValueCategory enum with the new three-group taxonomy
(STAMMDATEN / INPUTS / CALCULATED) and seeds the new indicator set.

Revision ID: c7f8a2b3d401
Revises: bddd708e0235
Create Date: 2026-04-20 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

from app.values.catalog import SEED_VALUES


revision = 'c7f8a2b3d401'
down_revision = 'bddd708e0235'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM llm_messages")
    op.execute("DELETE FROM llm_conversations")
    op.execute("DELETE FROM company_values")
    op.execute("DELETE FROM value_definitions")

    op.execute("ALTER TYPE valuecategory RENAME TO valuecategory_old")
    op.execute("CREATE TYPE valuecategory AS ENUM ('STAMMDATEN', 'INPUTS', 'CALCULATED')")
    op.execute(
        "ALTER TABLE value_definitions "
        "ALTER COLUMN category TYPE valuecategory USING category::text::valuecategory"
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
