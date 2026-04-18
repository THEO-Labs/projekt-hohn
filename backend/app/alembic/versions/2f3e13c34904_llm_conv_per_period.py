"""llm_conv_per_period

Revision ID: 2f3e13c34904
Revises: a1b2c3d4e5f6
Create Date: 2026-04-18 23:03:01.068361

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2f3e13c34904'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_conversations",
        sa.Column("period_type", sa.String(16), nullable=False, server_default="SNAPSHOT"),
    )
    op.add_column(
        "llm_conversations",
        sa.Column("period_year", sa.Integer(), nullable=True),
    )
    op.drop_constraint("uq_llm_conv", "llm_conversations", type_="unique")
    op.execute(
        "CREATE UNIQUE INDEX uq_llm_conv ON llm_conversations "
        "(company_id, value_key, period_type, COALESCE(period_year, -1))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_llm_conv")
    op.create_unique_constraint(
        "uq_llm_conv", "llm_conversations", ["company_id", "value_key"]
    )
    op.drop_column("llm_conversations", "period_year")
    op.drop_column("llm_conversations", "period_type")
