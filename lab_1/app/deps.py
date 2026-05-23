from __future__ import annotations

from fastapi import Request

from .session_service import SessionService


def get_sessions(request: Request) -> SessionService:
    return request.app.state.sessions


def get_mongo(request: Request):
    return request.app.state.mongo


def get_redis(request: Request):
    return request.app.state.redis


def get_cassandra(request: Request):
    return request.app.state.cassandra


def get_reactions(request: Request):
    return request.app.state.reactions


def get_reviews(request: Request):
    return request.app.state.reviews

