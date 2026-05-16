from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .indexes import ensure_mongo_indexes
from .routers.auth import router as auth_router
from .routers.events import router as events_router
from .routers.health import router as health_router
from .routers.session import router as session_router
from .routers.users import router as users_router
from .reactions_service import ReactionsService
from .reviews_service import ReviewsService
from .session_service import SessionService
from .settings import SettingsError, get_settings
from .storage import create_cassandra, create_mongo, create_redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    r = create_redis(s)
    m = create_mongo(s)
    c = create_cassandra(s)
    ensure_mongo_indexes(m)
    app.state.redis = r
    app.state.mongo = m
    app.state.cassandra = c
    app.state.sessions = SessionService(r, s.session_ttl)
    app.state.reactions = ReactionsService(
        c,
        r,
        cache_ttl=s.like_ttl,
        cassandra_consistency=s.cassandra_consistency,
    )
    app.state.reviews = ReviewsService(
        c,
        r,
        cache_ttl=s.event_reviews_ttl,
        cassandra_consistency=s.cassandra_consistency,
    )
    yield


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(session_router)
    app.include_router(users_router)
    app.include_router(auth_router)
    app.include_router(events_router)

    @app.exception_handler(404)
    async def not_found_handler(_request: Request, _exc):
        return JSONResponse(status_code=404, content={"status": "not_found"})

    @app.exception_handler(SettingsError)
    async def settings_handler(_request: Request, exc: SettingsError):
        return JSONResponse(status_code=503, content={"status": "misconfigured", "message": str(exc)})

    return app
