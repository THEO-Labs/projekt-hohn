import app.auth  # noqa: F401  # Modelle registrieren
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

    return app


app = create_app()
