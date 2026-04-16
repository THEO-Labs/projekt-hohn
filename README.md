# Turning Point Hohn-Rendite Tool

Internes Tool fuer Turning Point Investments zur Berechnung der erweiterten Hohn-Rendite.

## Lokal starten

Voraussetzungen: Docker, Python 3.12, Node 20+

```bash
docker compose up -d db
cd backend && uv sync && uv run alembic upgrade head
cd backend && uv run uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev
```

Backend: http://localhost:8000
Frontend: http://localhost:5173 (Dev), oder http://localhost:8000 (Prod-Build)

Siehe `docs/superpowers/specs/` fuer Architektur und `docs/superpowers/plans/` fuer Implementation Plans.
