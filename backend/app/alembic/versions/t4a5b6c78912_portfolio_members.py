"""portfolio members table

Revision ID: t4a5b6c78912
Revises: s2f3a4b78901
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "t4a5b6c78912"
down_revision = "s2f3a4b78901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_members",
        sa.Column("portfolio_id", UUID(as_uuid=True),
                  sa.ForeignKey("portfolios.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("portfolio_members")
