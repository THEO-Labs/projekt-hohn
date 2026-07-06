"""add detail-page values (revenue, eps_diluted, ocf, capex, balance-sheet splits, ps_ratio, margins)

Additive migration: inserts new value_definitions rows if they do not
exist yet. Existing company_values remain untouched.

Revision ID: q0d1e2f56789
Revises: p9c0d4e567
Create Date: 2026-07-06 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'q0d1e2f56789'
down_revision = 'p9c0d4e567'
branch_labels = None
depends_on = None


NEW_ROWS = [
    # (key, label_de, label_en, category, source_type, data_type, unit, sort_order)
    ("revenue",              "Umsatz",              "Revenue",             "NI_GROWTH", "API",        "NUMERIC", None, 42),
    ("eps_diluted",          "EPS (verwaessert)",   "EPS (Diluted)",       "NI_GROWTH", "API",        "NUMERIC", None, 43),
    ("ni_margin",            "Netto-Marge",         "NI Margin",           "NI_GROWTH", "CALCULATED", "NUMERIC", "%",  44),
    ("operating_cash_flow",  "Operativer Cashflow", "Operating Cash Flow", "FCF",       "API",        "NUMERIC", None, 32),
    ("capex",                "CapEx",               "CapEx",               "FCF",       "API",        "NUMERIC", None, 33),
    ("ocf_margin",           "OCF-Marge",           "OCF Margin",          "FCF",       "CALCULATED", "NUMERIC", "%",  34),
    ("fcf_margin",           "FCF-Marge",           "FCF Margin",          "FCF",       "CALCULATED", "NUMERIC", "%",  35),
    ("cash_and_equivalents", "Cash",                "Cash & Equivalents",  "CASH",      "API",        "NUMERIC", None, 53),
    ("st_investments",       "Kurzfr. Anlagen",     "ST Investments",      "CASH",      "API",        "NUMERIC", None, 54),
    ("st_debt",              "Kurzfr. Schulden",    "ST Debt",             "DEBT",      "API",        "NUMERIC", None, 55),
    ("lt_debt",              "Langfr. Schulden",    "LT Debt",             "DEBT",      "API",        "NUMERIC", None, 56),
    ("ps_ratio",             "KUV",                 "PS Ratio",            "VALUATION", "CALCULATED", "NUMERIC", None, 85),
]


def upgrade() -> None:
    conn = op.get_bind()
    stmt = sa.text(
        "INSERT INTO value_definitions "
        "(key, label_de, label_en, category, source_type, data_type, unit, sort_order) "
        "VALUES (:key, :label_de, :label_en, "
        "CAST(:category AS valuecategory), "
        "CAST(:source_type AS sourcetype), "
        "CAST(:data_type AS datatype), "
        ":unit, :sort_order) "
        "ON CONFLICT (key) DO NOTHING"
    )
    for row in NEW_ROWS:
        conn.execute(
            stmt,
            {
                "key": row[0],
                "label_de": row[1],
                "label_en": row[2],
                "category": row[3],
                "source_type": row[4],
                "data_type": row[5],
                "unit": row[6],
                "sort_order": row[7],
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    keys = [row[0] for row in NEW_ROWS]
    conn.execute(
        sa.text("DELETE FROM value_definitions WHERE key = ANY(:keys)"),
        {"keys": keys},
    )
