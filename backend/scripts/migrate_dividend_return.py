"""
One-time migration: delete all dividend_return values from DB.
After this, the next refresh will re-fetch and store as percent (e.g. 4.38 instead of 0.0438).

Run once after deploying the dividend_return normalization fix:
    cd backend && uv run python scripts/migrate_dividend_return.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import engine
from sqlalchemy import text

with engine.begin() as conn:
    result = conn.execute(
        text("DELETE FROM company_values WHERE value_key = 'dividend_return'")
    )
    print(f"Deleted {result.rowcount} dividend_return rows.")
