"""add numeric_value_adjusted + adjustments_note + adjustments_source columns

Revision ID: m6f7a89b0123
Revises: l5e6f7a89012
Create Date: 2026-05-13 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'm6f7a89b0123'
down_revision = 'l5e6f7a89012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_values",
        sa.Column("numeric_value_adjusted", sa.Numeric(precision=20, scale=6), nullable=True),
    )
    op.add_column(
        "company_values",
        sa.Column("adjustments_note", sa.Text(), nullable=True),
    )
    op.add_column(
        "company_values",
        sa.Column("adjustments_source", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_values", "adjustments_source")
    op.drop_column("company_values", "adjustments_note")
    op.drop_column("company_values", "numeric_value_adjusted")
