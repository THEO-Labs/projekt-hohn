"""backfill primary_method from source_name + dedup Q rows

Two cleanups:

(1) primary_method backfill:
    Older rows (from before primary_method existed) have NULL. The frontend
    then renders them as "unknown source". We derive primary_method from the
    source_name via pattern match. Idempotent.

(2) Deduplicate Q rows:
    Some Q rows exist twice for the same (company, key, period_type,
    period_year, is_forecast). Happened because legacy PDF-extract and
    Claude-recherche both wrote a row before the unique constraint was in
    place. Keep the newer / cleaner one (prefer primary_method='pdf' or
    'provider' with numeric_value not null, then latest fetched_at).

Revision ID: r1e2f3a67890
Revises: q0d1e2f56789
Create Date: 2026-07-06 22:00:00.000000
"""
from alembic import op


revision = 'r1e2f3a67890'
down_revision = 'q0d1e2f56789'
branch_labels = None
depends_on = None


PATTERN_TO_METHOD = [
    ("'PDF: %'", "'pdf'"),
    ("'PDF leer%'", "'web_guidance'"),
    ("'SEC EDGAR%'", "'provider'"),
    ("'Bloomberg%'", "'provider'"),
    ("'Yahoo%'", "'provider'"),
    ("'Manuell%'", "'manual'"),
    ("'Web-Guidance%'", "'web_guidance'"),
    ("'%Claude-Recherche%'", "'web_guidance'"),
    ("'%Claude-Q-Estimate%'", "'web_guidance'"),
    ("'%KI-Einsch%'", "'web_guidance'"),
    ("'Approximation%'", "'web_guidance'"),
    ("'%Derived Annual%'", "'calculated'"),
    ("'Per-Q-Aggregation%'", "'calculated'"),
]


def upgrade() -> None:
    # (1) primary_method backfill
    for pattern, method in PATTERN_TO_METHOD:
        op.execute(
            f"UPDATE company_values SET primary_method = {method} "
            f"WHERE primary_method IS NULL AND source_name LIKE {pattern}"
        )

    # (2) Duplicate Q-row cleanup. Keep the "best" row per
    # (company_id, value_key, period_type, period_year, is_forecast):
    #   prefer primary_method in (provider, pdf) with non-null value,
    #   then non-null value, then latest fetched_at.
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY company_id, value_key, period_type, period_year, is_forecast
                    ORDER BY
                        CASE WHEN primary_method IN ('provider', 'pdf') THEN 0 ELSE 1 END,
                        CASE WHEN numeric_value IS NOT NULL THEN 0 ELSE 1 END,
                        fetched_at DESC NULLS LAST,
                        id
                ) AS rn
            FROM company_values
            WHERE period_type IN ('Q1', 'Q2', 'Q3', 'Q4', 'FY')
        )
        DELETE FROM company_values
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1);
        """
    )


def downgrade() -> None:
    # No reasonable downgrade: original NULLs were an accident, and deleted
    # duplicate rows are lost.
    pass
