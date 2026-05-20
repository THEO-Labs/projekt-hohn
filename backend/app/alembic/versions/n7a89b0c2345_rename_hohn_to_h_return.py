"""rename Hohn-Rendite labels to H-Return (keys bleiben fuer Datenkonsistenz)

Revision ID: n7a89b0c2345
Revises: m6f7a89b0123
Create Date: 2026-05-20 10:00:00.000000

"""
from alembic import op


revision = 'n7a89b0c2345'
down_revision = 'm6f7a89b0123'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE value_definitions
        SET label_de = 'H-Return (einfach)', label_en = 'H-Return (simple)'
        WHERE key = 'hohn_return_simple'
    """)
    op.execute("""
        UPDATE value_definitions
        SET label_de = 'H-Return (detailed)', label_en = 'H-Return (detailed)'
        WHERE key = 'hohn_return_detailed'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE value_definitions
        SET label_de = 'Hohn-Rendite (einfach)', label_en = 'Hohn Return (simple)'
        WHERE key = 'hohn_return_simple'
    """)
    op.execute("""
        UPDATE value_definitions
        SET label_de = 'Hohn-Rendite (detailed)', label_en = 'Hohn Return (detailed)'
        WHERE key = 'hohn_return_detailed'
    """)
