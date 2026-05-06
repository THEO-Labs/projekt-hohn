"""seed_value_definitions

NOTE: ursprueglich hat diese Migration `SEED_VALUES` aus dem Live-Code
geseeded. Da spaetere Migrationen (d5f7e9b12345) den Katalog komplett
zuruecksetzen + neu aufbauen, ist diese Migration fuer Fresh-Installs
ein No-op. Auf bestehenden Prod-DBs ist sie laengst angewendet — der
geaenderte upgrade()-Body laeuft dort nicht erneut.

Revision ID: 0a4be665e23c
Revises: 82bf9e484a46
"""


revision = '0a4be665e23c'
down_revision = '82bf9e484a46'
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
