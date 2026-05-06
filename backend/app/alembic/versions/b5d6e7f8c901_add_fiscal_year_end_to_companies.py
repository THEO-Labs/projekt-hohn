"""backfill columns added via create_all() but never migrated

Sammeln: alle Modell-Spalten die irgendwann ueber Base.metadata.create_all()
auf Prod gelandet sind, aber keine eigene Migration hatten. Fresh-Install
crashte sonst beim ersten INSERT mit UndefinedColumn.

Aktuell:
  companies.fiscal_year_end_month, companies.fiscal_year_end_day (Integer)
  company_values.from_ir_pdf (Boolean, default false)

Defensiv: pro Spalte pruefen, ob sie schon existiert (Prod-Fall) und
sonst anlegen.

Revision ID: b5d6e7f8c901
Revises: a4c5d6e7b801
Create Date: 2026-05-06 13:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b5d6e7f8c901'
down_revision = 'a4c5d6e7b801'
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "companies", "fiscal_year_end_month"):
        op.add_column("companies", sa.Column("fiscal_year_end_month", sa.Integer(), nullable=True))
    if not _has_column(bind, "companies", "fiscal_year_end_day"):
        op.add_column("companies", sa.Column("fiscal_year_end_day", sa.Integer(), nullable=True))
    if not _has_column(bind, "company_values", "from_ir_pdf"):
        op.add_column(
            "company_values",
            sa.Column("from_ir_pdf", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )

    # source_name war urspruenglich VARCHAR(128), Modell zeigt jetzt 512 —
    # PDF-Quellen-Strings ueberschreiten 128 schnell ("PDF: ... — kein Wert: ...").
    op.execute("ALTER TABLE company_values ALTER COLUMN source_name TYPE varchar(512)")


def downgrade() -> None:
    op.execute("ALTER TABLE company_values ALTER COLUMN source_name TYPE varchar(128)")
    op.drop_column("company_values", "from_ir_pdf")
    op.drop_column("companies", "fiscal_year_end_day")
    op.drop_column("companies", "fiscal_year_end_month")
