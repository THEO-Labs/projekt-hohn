"""re-add forecast_alternates JSONB

Im Estimate-Mode (laufendes FY) zeigen wir doch wieder beide Werte parallel —
Q-Faktor-Proxy oben, Web-Recherche darunter zum Vergleich. Backend rechnet
immer beide, primary geht in numeric_value, der zweite Wert in
forecast_alternates JSONB.

Revision ID: i2b3c4d5e601
Revises: h1a2b3c4d501
Create Date: 2026-05-07 13:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'i2b3c4d5e601'
down_revision = 'h1a2b3c4d501'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_values",
        sa.Column("forecast_alternates", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_values", "forecast_alternates")
