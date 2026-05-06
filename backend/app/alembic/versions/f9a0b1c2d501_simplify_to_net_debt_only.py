"""simplify cash/debt to net_debt-only

User-Wunsch: die 5 Bilanz-Sub-Inputs (cash_and_equivalents,
marketable_securities_st, marketable_securities_lt, lease_liabilities,
long_term_debt) und die 3 Aggregate (cash_sum, debt_sum, ev) komplett
raus. Stattdessen wird `net_debt` direkt aus dem Annual Report extrahiert
(Highlights / Management Report / Notes-Reconciliation).

Aenderungen:
- DELETE company_values + value_definitions fuer die 8 wegfallenden Keys.
- net_debt: source_type von CALCULATED → API (kommt jetzt aus Extraktion).
- net_debt + net_debt_change + net_debt_change_pct: category von DELTA_ND → DEBT.
- DEBT-Kategorie wird damit die "Net Debt"-Sektion. CASH + DELTA_ND bleiben
  als Enum-Werte (Bestandsdaten-Schutz), aber ungenutzt.

Revision ID: f9a0b1c2d501
Revises: e8a9b0c1f401
Create Date: 2026-05-06 18:00:00.000000
"""
from alembic import op


revision = 'f9a0b1c2d501'
down_revision = 'e8a9b0c1f401'
branch_labels = None
depends_on = None


_REMOVE_KEYS = (
    "cash_and_equivalents",
    "marketable_securities_st",
    "marketable_securities_lt",
    "lease_liabilities",
    "long_term_debt",
    "cash_sum",
    "debt_sum",
    "ev",
)


def upgrade() -> None:
    # 1. Bestandsdaten in company_values entfernen.
    keys_sql = ", ".join(f"'{k}'" for k in _REMOVE_KEYS)
    op.execute(f"DELETE FROM company_values WHERE value_key IN ({keys_sql})")

    # 2. Auch zugehoerige LLM-Conversations + Messages entfernen
    #    (sonst dangling Verweise auf nicht mehr existierende value_keys).
    op.execute(f"""
        DELETE FROM llm_messages WHERE conversation_id IN (
            SELECT id FROM llm_conversations WHERE value_key IN ({keys_sql})
        )
    """)
    op.execute(f"DELETE FROM llm_conversations WHERE value_key IN ({keys_sql})")

    # 3. Aus dem Catalog raus.
    op.execute(f"DELETE FROM value_definitions WHERE key IN ({keys_sql})")

    # 4. net_debt: source_type von CALCULATED → API.
    op.execute("UPDATE value_definitions SET source_type='API' WHERE key='net_debt'")

    # 5. Net-Debt-Familie umkategorisieren: DELTA_ND → DEBT.
    op.execute("""
        UPDATE value_definitions SET category='DEBT'
        WHERE key IN ('net_debt', 'net_debt_change', 'net_debt_change_pct')
    """)

    # 6. Sort-orders neu fuer DEBT-Sektion (Hohn-Faktor zuerst).
    op.execute("UPDATE value_definitions SET sort_order=50 WHERE key='net_debt_change_pct'")
    op.execute("UPDATE value_definitions SET sort_order=51 WHERE key='net_debt_change'")
    op.execute("UPDATE value_definitions SET sort_order=52 WHERE key='net_debt'")

    # 7. Bestehende net_debt-CompanyValue-Rows haben source_name='Calculated'.
    #    Loeschen, damit Re-Extraktion eine API-Quelle setzt.
    op.execute("DELETE FROM company_values WHERE value_key='net_debt' AND source_name='Calculated'")


def downgrade() -> None:
    # Nicht reversibel ohne Datenverlust — Bestand der Bilanz-Subkomponenten
    # ist nach upgrade() weg. NotImplemented akzeptiert.
    raise NotImplementedError("One-way simplification; no downgrade.")
