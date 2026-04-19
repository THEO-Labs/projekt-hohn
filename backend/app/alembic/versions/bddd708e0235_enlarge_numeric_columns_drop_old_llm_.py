"""enlarge_numeric_columns_drop_old_llm_constraint

Revision ID: bddd708e0235
Revises: 2f3e13c34904
Create Date: 2026-04-19 17:09:06.111710

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'bddd708e0235'
down_revision = '2f3e13c34904'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE llm_conversations DROP CONSTRAINT IF EXISTS uq_llm_conv_company_value")
    op.alter_column(
        "company_values", "numeric_value",
        type_=sa.Numeric(25, 6), existing_type=sa.Numeric(20, 6), existing_nullable=True,
    )
    op.alter_column(
        "llm_messages", "score_suggestion",
        type_=sa.Numeric(25, 6), existing_type=sa.Numeric(20, 6), existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "llm_messages", "score_suggestion",
        type_=sa.Numeric(20, 6), existing_type=sa.Numeric(25, 6), existing_nullable=True,
    )
    op.alter_column(
        "company_values", "numeric_value",
        type_=sa.Numeric(20, 6), existing_type=sa.Numeric(25, 6), existing_nullable=True,
    )
