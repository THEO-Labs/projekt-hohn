"""llm_unique_conv_source_field

Revision ID: a1b2c3d4e5f6
Revises: 96acdd6be79e
Branch Labels: None
Depends On: None

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = '96acdd6be79e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('llm_messages', sa.Column('source', sa.String(length=16), nullable=True))
    op.create_unique_constraint('uq_llm_conv', 'llm_conversations', ['company_id', 'value_key'])


def downgrade() -> None:
    op.drop_constraint('uq_llm_conv', 'llm_conversations', type_='unique')
    op.drop_column('llm_messages', 'source')
