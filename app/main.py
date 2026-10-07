from __future__ import annotations

from fastapi import FastAPI

from .api.routes import router
from .config import Settings, get_settings
from .db import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Geospatial File Measurement API",
        version="1.0.0",
        description="Upload a zipped Shapefile or a KML file and get per-feature area/length measurements.",
    )
    app.state.settings = settings
    app.state.db = Database(settings.db_path)
    app.include_router(router)

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    return app


app = create_app()
