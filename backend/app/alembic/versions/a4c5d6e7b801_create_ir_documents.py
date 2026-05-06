"""create ir_documents table

Backfill: ir_documents wurde im Code-Modell ergaenzt, aber nie als
Alembic-Migration angelegt — auf Prod existierte die Tabelle bisher zufaellig
(Base.metadata.create_all in einem frueheren Stand). Bei einem Fresh-Install
fehlte sie. Diese Migration legt sie sauber an.

Revision ID: a4c5d6e7b801
Revises: f8a9b1c23456
Create Date: 2026-05-06 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'a4c5d6e7b801'
down_revision = 'f8a9b1c23456'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("ir_documents"):
        # Prod hat die Tabelle bereits durch frueheres create_all — nichts zu tun.
        return

    document_type = postgresql.ENUM(
        'ANNUAL_REPORT', 'FORM_10K', 'FORM_20F', 'EARNINGS_RELEASE',
        'QUARTERLY_REPORT', 'INVESTOR_PRESENTATION', 'OTHER',
        name='documenttype',
        create_type=False,
    )
    period_coverage = postgresql.ENUM(
        'FY', 'Q1', 'Q2', 'Q3', 'Q4', 'H1', 'H2',
        name='periodcoverage',
        create_type=False,
    )
    extraction_status = postgresql.ENUM(
        'PENDING', 'EXTRACTING', 'DONE', 'FAILED',
        name='extractionstatus',
        create_type=False,
    )
    document_type.create(bind, checkfirst=True)
    period_coverage.create(bind, checkfirst=True)
    extraction_status.create(bind, checkfirst=True)

    op.create_table(
        'ir_documents',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('company_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('document_type', document_type, nullable=False),
        sa.Column('period_coverage', period_coverage, nullable=False),
        sa.Column('period_year', sa.Integer(), nullable=False),
        sa.Column('display_name', sa.String(length=255), nullable=False),
        sa.Column('original_filename', sa.String(length=512), nullable=False),
        sa.Column('storage_path', sa.String(length=512), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('mime_type', sa.String(length=64), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('uploaded_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('extraction_status', extraction_status, nullable=False, server_default='PENDING'),
        sa.Column('extracted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('extraction_results', postgresql.JSONB(), nullable=True),
        sa.Column('extraction_error', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['uploaded_by_user_id'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_ir_documents_company_id', 'ir_documents', ['company_id'])


def downgrade() -> None:
    op.drop_index('ix_ir_documents_company_id', table_name='ir_documents')
    op.drop_table('ir_documents')
    op.execute("DROP TYPE IF EXISTS extractionstatus")
    op.execute("DROP TYPE IF EXISTS periodcoverage")
    op.execute("DROP TYPE IF EXISTS documenttype")
