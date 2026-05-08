"""add primary_method + last_refresh_attempt to company_values

Bulletproof-Review-Fixes:
- primary_method: explizites Methoden-Marker fuer das Frontend statt
  fragiles Source-Name-String-Match (web_guidance/q_factor_proxy/pdf/
  manual/provider/calculated).
- last_refresh_attempt: Stale-Indikator. Bei Refresh-Fail ohne neuen Wert
  bleibt fetched_at unveraendert, aber last_refresh_attempt wird gesetzt.
  Frontend kann 'Daten sind stale' anzeigen wenn last_refresh_attempt >>
  fetched_at.

Revision ID: j3c4d5e6f701
Revises: i2b3c4d5e601
Create Date: 2026-05-08 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'j3c4d5e6f701'
down_revision = 'i2b3c4d5e601'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_values",
        sa.Column("primary_method", sa.String(32), nullable=True),
    )
    op.add_column(
        "company_values",
        sa.Column("last_refresh_attempt", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_values", "last_refresh_attempt")
    op.drop_column("company_values", "primary_method")
