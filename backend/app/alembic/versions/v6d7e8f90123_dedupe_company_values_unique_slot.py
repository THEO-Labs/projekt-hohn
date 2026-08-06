"""dedupe company_values + Unique-Index pro Zelle

Der Code nimmt Eindeutigkeit pro (company_id, value_key, period_type,
period_year, is_forecast) an, die DB hatte aber keinen Unique-Index —
Races zwischen Batch-Threads/Gap-Fill erzeugten Duplikate.

(1) Dedupe: pro Slot ueberlebt die beste Zeile (SQL-Window-Function,
    Rangfolge wie app.values.dedupe).
(2) Unique-Index als Expression-Index: period_year ist NULL bei SNAPSHOT,
    coalesce(period_year, -1) macht NULL-Slots ebenfalls eindeutig.

Revision ID: v6d7e8f90123
Revises: u5c6d7e89012
Create Date: 2026-08-06
"""
from alembic import op

revision = "v6d7e8f90123"
down_revision = "u5c6d7e89012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Dedupe als reines SQL — kein ORM-Import: das volle CompanyValue-Modell
    # wuerde Fresh-DB-Upgrades brechen, sobald das Modell neuere Spalten hat
    # als der Schema-Stand dieser Revision. Rangfolge identisch zu
    # app.values.dedupe (Wert > Manual+Wert > PDF+Wert > Methoden-Rang >
    # fetched_at > id).
    op.execute(
        """
        DELETE FROM company_values WHERE id IN (
          SELECT id FROM (
            SELECT id, row_number() OVER (
              PARTITION BY company_id, value_key, period_type, coalesce(period_year, -1), is_forecast
              ORDER BY
                (numeric_value IS NOT NULL) DESC,
                (manually_overridden AND numeric_value IS NOT NULL) DESC,
                (from_ir_pdf AND numeric_value IS NOT NULL) DESC,
                CASE primary_method
                  WHEN 'provider' THEN 7 WHEN 'two_stage_confirmed' THEN 6
                  WHEN 'two_stage_verified' THEN 5 WHEN 'manual' THEN 4
                  WHEN 'calculated' THEN 3 WHEN 'web_guidance' THEN 2
                  WHEN 'not_found' THEN 0 ELSE 1 END DESC,
                fetched_at DESC NULLS LAST, id DESC
            ) rn FROM company_values
          ) ranked WHERE rn > 1
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_company_values_slot ON company_values "
        "(company_id, value_key, period_type, coalesce(period_year, -1), is_forecast)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_company_values_slot")
