from __future__ import annotations

from fastapi import Request

from .session_service import SessionService


def get_sessions(request: Request) -> SessionService:
    return request.app.state.sessions

