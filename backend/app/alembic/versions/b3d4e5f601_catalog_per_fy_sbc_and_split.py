"""catalog_per_fy_sbc_and_split

NOTE: Original-Inhalt war ein Katalog-Reset auf Basis des Live-SEED_VALUES.
Spaeter aufgeloest in d5f7e9b12345 (final_catalog_hohn_simple_detailed),
das den Katalog komplett neu setzt. Diese Migration ist daher fuer
Fresh-Installs ein No-op.

Revision ID: b3d4e5f601
Revises: a2c3d4e5f601
"""


revision = 'b3d4e5f601'
down_revision = 'a2c3d4e5f601'
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
