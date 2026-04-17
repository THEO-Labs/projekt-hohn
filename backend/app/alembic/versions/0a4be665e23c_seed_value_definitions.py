"""seed_value_definitions

Revision ID: 0a4be665e23c
Revises: 82bf9e484a46
Create Date: 2026-04-17 08:57:43.603805

"""
from alembic import op
import sqlalchemy as sa

from app.values.catalog import SEED_VALUES


# revision identifiers, used by Alembic.
revision = '0a4be665e23c'
down_revision = '82bf9e484a46'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    for row in SEED_VALUES:
        conn.execute(
            sa.text(
                "INSERT INTO value_definitions "
                "(key, label_de, label_en, category, source_type, data_type, unit, sort_order) "
                "VALUES (:key, :label_de, :label_en, "
                "CAST(:category AS valuecategory), "
                "CAST(:source_type AS sourcetype), "
                "CAST(:data_type AS datatype), "
                ":unit, :sort_order) "
                "ON CONFLICT (key) DO UPDATE SET "
                "label_de = EXCLUDED.label_de, "
                "label_en = EXCLUDED.label_en, "
                "category = EXCLUDED.category, "
                "source_type = EXCLUDED.source_type, "
                "data_type = EXCLUDED.data_type, "
                "unit = EXCLUDED.unit, "
                "sort_order = EXCLUDED.sort_order"
            ),
            {
                "key": row["key"],
                "label_de": row["label_de"],
                "label_en": row["label_en"],
                "category": row["category"],
                "source_type": row["source_type"],
                "data_type": row["data_type"],
                "unit": row.get("unit"),
                "sort_order": row["sort_order"],
            },
        )


def downgrade() -> None:
    keys = [row["key"] for row in SEED_VALUES]
    for key in keys:
        op.execute(sa.text("DELETE FROM value_definitions WHERE key = :key").bindparams(key=key))
