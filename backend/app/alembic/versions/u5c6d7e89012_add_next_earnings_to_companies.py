"""next earnings release pro Firma

companies bekommt zwei nullable Spalten:
  next_earnings_date   -- naechster bekannter Earnings-Termin (Yahoo-Kalender)
  earnings_checked_at  -- wann zuletzt geprueft (24h-TTL im Daily-Refresh)

Revision ID: u5c6d7e89012
Revises: t4a5b6c78912
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op

revision = "u5c6d7e89012"
down_revision = "t4a5b6c78912"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("next_earnings_date", sa.Date(), nullable=True))
    op.add_column("companies", sa.Column("earnings_checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "earnings_checked_at")
    op.drop_column("companies", "next_earnings_date")
