"""reset_catalog_v3

Second catalog reset to match final customer spec (Stammdaten +
per-FY inputs + ratios including EV/OpCF, PE LTM adj, PEG, etc.).
Wipes values and chat history, keeps enum shape the same
(STAMMDATEN / INPUTS / CALCULATED).

Revision ID: e8d9f0a12345
Revises: c7f8a2b3d401
Create Date: 2026-04-21 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

from app.values.catalog import SEED_VALUES


revision = 'e8d9f0a12345'
down_revision = 'c7f8a2b3d401'
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
