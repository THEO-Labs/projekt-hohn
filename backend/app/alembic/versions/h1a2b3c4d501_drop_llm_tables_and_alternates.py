"""drop llm_conversations, llm_messages, forecast_alternates

Tabula-rasa: Chat-Funktion komplett raus. Estimate ist jetzt single-source
(Web-Guidance praeferiert, Q-Faktor-Proxy als Fallback). Recherche persistiert
direkt als CompanyValue mit manually_overridden=True statt als Chat-Konversation.

Revision ID: h1a2b3c4d501
Revises: g0a1b2c3d401
Create Date: 2026-05-07 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'h1a2b3c4d501'
down_revision = 'g0a1b2c3d401'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("company_values", "forecast_alternates")
    op.execute("DROP TABLE IF EXISTS llm_messages CASCADE")
    op.execute("DROP TABLE IF EXISTS llm_conversations CASCADE")


def downgrade() -> None:
    raise NotImplementedError("tabula-rasa drop — kein automatisches Downgrade")
