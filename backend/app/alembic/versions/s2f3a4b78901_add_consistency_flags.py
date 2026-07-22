"""add consistency_flags to company_values

Revision ID: s2f3a4b78901
Revises: r1e2f3a67890
Create Date: 2026-07-22
"""

import sqlalchemy as sa
from alembic import op

revision = "s2f3a4b78901"
down_revision = "r1e2f3a67890"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_values",
        sa.Column("consistency_flags", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_values", "consistency_flags")
