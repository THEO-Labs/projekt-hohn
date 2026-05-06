"""restore unique index on company_values

Migration 96acdd6be79e (LLM-tables) hatte einen alembic-autogenerate-Bug
und droppte das uq_company_values Unique-Index, das in dc287206e3c0
angelegt wurde. Ohne den Index entstehen Duplikate (z.B. wenn beim
historical-stammdaten-Fetch + spaeterer Refresh zwei Rows fuer
(company, market_cap, FY, year, is_forecast=False) angelegt werden).
PDF-Extraction crashed dann mit MultipleResultsFound.

Diese Migration:
1. dedupliziert vorhandene Rows (laesst die juengste pro
   (company_id, value_key, period_type, period_year, is_forecast) stehen),
2. legt das Unique-Index sauber wieder an.

Revision ID: d7f8a9b1e301
Revises: c6e7f8a9d201
Create Date: 2026-05-06 14:30:00.000000
"""
from alembic import op


revision = 'd7f8a9b1e301'
down_revision = 'c6e7f8a9d201'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Dedupe: pro Schluessel-Tuple den juengsten Row (max fetched_at, fallback id)
    # behalten, alle anderen loeschen.
    op.execute("""
        DELETE FROM company_values
        WHERE id NOT IN (
            SELECT DISTINCT ON (
                company_id, value_key, period_type,
                COALESCE(period_year, -1), is_forecast
            ) id
            FROM company_values
            ORDER BY
                company_id, value_key, period_type,
                COALESCE(period_year, -1), is_forecast,
                fetched_at DESC NULLS LAST, id DESC
        )
    """)

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_company_values
        ON company_values
        (company_id, value_key, period_type, COALESCE(period_year, -1), is_forecast)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_company_values")
