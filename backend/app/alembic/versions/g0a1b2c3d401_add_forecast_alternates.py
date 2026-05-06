"""add forecast_alternates JSONB to company_values

Im Estimate-Modus wollen wir beide Wege parallel rechnen — Web-Guidance
(Claude Web-Search, Management Outlook / Konsens) und Q-Faktor-Proxy
(Hochrechnung aus Quartalszahlen). Primary-Wert bleibt in numeric_value,
die alternative Methode (mit eigener Source + value) landet als JSON
im neuen forecast_alternates-Feld. Frontend zeigt beide untereinander.

Format des JSONB:
[
  { "method": "q_factor", "value": "3858000000", "currency": "EUR",
    "source": "Proxy (Q-Faktor): FY2025 × Faktor 0.7390" }
]
(Liste, damit zukuenftig mehr als zwei Methoden moeglich.)

Revision ID: g0a1b2c3d401
Revises: f9a0b1c2d501
Create Date: 2026-05-06 17:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'g0a1b2c3d401'
down_revision = 'f9a0b1c2d501'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_values",
        sa.Column("forecast_alternates", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_values", "forecast_alternates")
