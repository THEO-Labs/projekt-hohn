"""widen company_values.source_name to 512

Original-Migration 82bf9e484a46 hat source_name als VARCHAR(128) angelegt.
Das Modell zeigt heute String(512). PDF-Source-Strings
'PDF: <doc> — kein Wert: <reason>' uebersteigen 128 schnell und der
Worker crashte mit StringDataRightTruncation.

Revision ID: c6e7f8a9d201
Revises: b5d6e7f8c901
Create Date: 2026-05-06 14:00:00.000000
"""
from alembic import op


revision = 'c6e7f8a9d201'
down_revision = 'b5d6e7f8c901'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE company_values ALTER COLUMN source_name TYPE varchar(512)")


def downgrade() -> None:
    op.execute("ALTER TABLE company_values ALTER COLUMN source_name TYPE varchar(128)")
