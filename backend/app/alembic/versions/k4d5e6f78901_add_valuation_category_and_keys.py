"""add VALUATION category + keys (pe_ratio, ev_ebitda, ebitda) + move actual_return/fcf_yield

Revision ID: k4d5e6f78901
Revises: j3c4d5e6f701
Create Date: 2026-05-12 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'k4d5e6f78901'
down_revision = 'j3c4d5e6f701'
branch_labels = None
depends_on = None


NEW_KEYS = [
    {"key": "pe_ratio", "label_de": "KGV", "label_en": "P/E Ratio", "category": "VALUATION", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 81},
    {"key": "ev_ebitda", "label_de": "EV / EBITDA", "label_en": "EV / EBITDA", "category": "VALUATION", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 82},
    {"key": "ebitda", "label_de": "EBITDA", "label_en": "EBITDA", "category": "VALUATION", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 84},
]


def upgrade() -> None:
    # 1. Enum erweitern um VALUATION
    op.execute("ALTER TYPE valuecategory ADD VALUE IF NOT EXISTS 'VALUATION'")

    # ENUM-Erweiterungen muessen in eigener Transaction sein bevor sie in
    # gleicher Migration als WHERE-Filter benutzt werden koennen (Postgres-Quirk).
    # Workaround: COMMIT der laufenden Transaction.
    op.execute("COMMIT")
    op.execute("BEGIN")

    # 2. fcf_yield + actual_return in VALUATION verschieben + sort_order anpassen
    op.execute(
        "UPDATE value_definitions SET category = CAST('VALUATION' AS valuecategory), sort_order = 80 "
        "WHERE key = 'actual_return'"
    )
    op.execute(
        "UPDATE value_definitions SET category = CAST('VALUATION' AS valuecategory), sort_order = 83 "
        "WHERE key = 'fcf_yield'"
    )

    # 3. Neue Keys einfuegen (pe_ratio, ev_ebitda, ebitda) — idempotent via ON CONFLICT
    conn = op.get_bind()
    for row in NEW_KEYS:
        conn.execute(
            sa.text(
                "INSERT INTO value_definitions "
                "(key, label_de, label_en, category, source_type, data_type, unit, sort_order) "
                "VALUES (:key, :label_de, :label_en, "
                "CAST(:category AS valuecategory), "
                "CAST(:source_type AS sourcetype), "
                "CAST(:data_type AS datatype), "
                ":unit, :sort_order) "
                "ON CONFLICT (key) DO UPDATE SET "
                "label_de=EXCLUDED.label_de, label_en=EXCLUDED.label_en, "
                "category=EXCLUDED.category, source_type=EXCLUDED.source_type, "
                "data_type=EXCLUDED.data_type, unit=EXCLUDED.unit, "
                "sort_order=EXCLUDED.sort_order"
            ),
            row,
        )


def downgrade() -> None:
    op.execute("DELETE FROM value_definitions WHERE key IN ('pe_ratio', 'ev_ebitda', 'ebitda')")
    op.execute(
        "UPDATE value_definitions SET category = CAST('HOHN_RETURN' AS valuecategory), sort_order = 72 "
        "WHERE key = 'actual_return'"
    )
    op.execute(
        "UPDATE value_definitions SET category = CAST('FCF' AS valuecategory), sort_order = 30 "
        "WHERE key = 'fcf_yield'"
    )
