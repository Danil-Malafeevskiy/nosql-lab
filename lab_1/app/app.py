from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .routers.health import router as health_router
from .settings import SettingsError


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(health_router)

    @app.exception_handler(404)
    async def not_found_handler(_request: Request, _exc):
        return JSONResponse(status_code=404, content={"status": "not_found"})

    @app.exception_handler(SettingsError)
    async def settings_handler(_request: Request, exc: SettingsError):
        return JSONResponse(status_code=503, content={"status": "misconfigured", "message": str(exc)})

    return app

