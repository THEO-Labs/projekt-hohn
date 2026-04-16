import app.auth  # noqa: F401  # Modelle registrieren
import app.portfolios  # noqa: F401
import app.companies  # noqa: F401
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings


def create_app() -> FastAPI:
    app = FastAPI(title="Hohn-Rendite Tool")

    if settings.origins_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.origins_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    from app.auth.routes import router as auth_router
    app.include_router(auth_router)

    from app.portfolios.routes import router as portfolios_router
    app.include_router(portfolios_router)

    from app.companies.routes import portfolio_scoped, company_router
    app.include_router(portfolio_scoped)
    app.include_router(company_router)

    return app


app = create_app()
