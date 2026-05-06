"""reset_catalog_new_formula

NOTE: Erster der diversen Katalog-Resets. Wurde in d5f7e9b12345 endgueltig
abgeloest. Fuer Fresh-Installs ein No-op (das Enum/Seed-Setup macht
d5f7e9b12345 vollstaendig). Auf Prod laengst angewendet.

Revision ID: c7f8a2b3d401
Revises: bddd708e0235
"""


revision = 'c7f8a2b3d401'
down_revision = 'bddd708e0235'
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
