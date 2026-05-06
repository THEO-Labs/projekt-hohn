"""split BUYBACKS_SBC → BUYBACKS + SBC and CASH_DEBT → CASH + DEBT

User-Wunsch: SBC und Buybacks sind zwei verschiedene Themen, nicht in
einer gemeinsamen Sektion mischen. Cash und Debt ebenso trennen.

Aenderungen:
- ValueCategory enum erweitert um CASH, DEBT, SBC, BUYBACKS.
  CASH_DEBT + BUYBACKS_SBC bleiben im Enum (Bestandsdaten-Schutz), werden
  aber nicht mehr aktiv vergeben.
- Bestehende value_definitions-Zeilen werden umkategorisiert:
    CASH_DEBT (cash_*, marketable_*) → CASH
    CASH_DEBT (lease, long_term_debt, debt_sum, ev) → DEBT
    BUYBACKS_SBC (sbc, sbc_yield) → SBC
    BUYBACKS_SBC (buyback_*, net_buyback*) → BUYBACKS
- sort_order bei Buybacks neu: Volume → Net Buyback → /MCap → Net/MCap
- sort_order bei SBC: yield zuerst, dann input

Revision ID: e8a9b0c1f401
Revises: d7f8a9b1e301
Create Date: 2026-05-06 16:00:00.000000
"""
from alembic import op


revision = 'e8a9b0c1f401'
down_revision = 'd7f8a9b1e301'
branch_labels = None
depends_on = None


_NEW_VALUES = ("CASH", "DEBT", "SBC", "BUYBACKS")


def upgrade() -> None:
    # 1. Enum erweitern (idempotent — IF NOT EXISTS).
    # ALTER TYPE ADD VALUE darf nicht in einer Transaktion laufen → autocommit_block.
    with op.get_context().autocommit_block():
        for v in _NEW_VALUES:
            op.execute(f"ALTER TYPE valuecategory ADD VALUE IF NOT EXISTS '{v}'")

    # 2. Datensatz-Umkategorisierung — Keys hardcoded weil Migration langlebig sein soll.
    op.execute("UPDATE value_definitions SET category='CASH'      WHERE key IN ('cash_sum','cash_and_equivalents','marketable_securities_st','marketable_securities_lt')")
    op.execute("UPDATE value_definitions SET category='DEBT'      WHERE key IN ('debt_sum','lease_liabilities','long_term_debt','ev')")
    op.execute("UPDATE value_definitions SET category='SBC'       WHERE key IN ('sbc','sbc_yield')")
    op.execute("UPDATE value_definitions SET category='BUYBACKS'  WHERE key IN ('buyback_volume','buyback_yield','net_buyback','net_buyback_yield')")

    # 3. Sort-Order anpassen damit die UI-Reihenfolge (Volume → Net → /MCap → Net/MCap) stimmt.
    op.execute("UPDATE value_definitions SET sort_order=19 WHERE key='sbc_yield'")
    op.execute("UPDATE value_definitions SET sort_order=20 WHERE key='sbc'")
    op.execute("UPDATE value_definitions SET sort_order=25 WHERE key='buyback_volume'")
    op.execute("UPDATE value_definitions SET sort_order=26 WHERE key='net_buyback'")
    op.execute("UPDATE value_definitions SET sort_order=27 WHERE key='buyback_yield'")
    op.execute("UPDATE value_definitions SET sort_order=28 WHERE key='net_buyback_yield'")


def downgrade() -> None:
    op.execute("UPDATE value_definitions SET category='CASH_DEBT'    WHERE key IN ('cash_sum','cash_and_equivalents','marketable_securities_st','marketable_securities_lt','debt_sum','lease_liabilities','long_term_debt','ev')")
    op.execute("UPDATE value_definitions SET category='BUYBACKS_SBC' WHERE key IN ('sbc','sbc_yield','buyback_volume','buyback_yield','net_buyback','net_buyback_yield')")
    op.execute("UPDATE value_definitions SET sort_order=20 WHERE key='sbc_yield'")
    op.execute("UPDATE value_definitions SET sort_order=21 WHERE key='net_buyback_yield'")
    op.execute("UPDATE value_definitions SET sort_order=22 WHERE key='buyback_yield'")
    op.execute("UPDATE value_definitions SET sort_order=23 WHERE key='net_buyback'")
    op.execute("UPDATE value_definitions SET sort_order=24 WHERE key='sbc'")
    op.execute("UPDATE value_definitions SET sort_order=25 WHERE key='buyback_volume'")
    # ENUM-Werte werden nicht entfernt (PostgreSQL unterstuetzt DROP VALUE nicht ohne weiteres).
