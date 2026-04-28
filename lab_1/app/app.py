from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .routers.health import router as health_router
from .routers.session import router as session_router
from .session_service import SessionService
from .settings import SettingsError, get_settings
from .storage import create_redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    r = create_redis(s)
    app.state.redis = r
    app.state.sessions = SessionService(r, s.session_ttl)
    yield


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(session_router)

    @app.exception_handler(404)
    async def not_found_handler(_request: Request, _exc):
        return JSONResponse(status_code=404, content={"status": "not_found"})

    @app.exception_handler(SettingsError)
    async def settings_handler(_request: Request, exc: SettingsError):
        return JSONResponse(status_code=503, content={"status": "misconfigured", "message": str(exc)})

    return app
