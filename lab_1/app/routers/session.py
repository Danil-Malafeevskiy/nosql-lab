from __future__ import annotations

import redis
from fastapi import APIRouter, Depends, Request

from ..deps import get_sessions
from ..http import cookie_refresh, extract_sid_cookie, resp_empty, resp_json
from ..session_service import SessionService
from ..utils import utc_now_rfc3339


router = APIRouter()


@router.post(
    "/session",
    summary="Создать/обновить анонимную сессию",
    description="Можно вызвать без cookie. Если cookie `X-Session-Id` уже есть, сессия будет продлена; иначе создана новая.",
    responses={
        201: {"description": "Новая сессия создана (`Set-Cookie`)"},
        200: {"description": "Текущая сессия продлена (`Set-Cookie`)"},
    },
    openapi_extra={
        "parameters": [
            {
                "name": "Cookie",
                "in": "header",
                "required": False,
                "schema": {"type": "string"},
                "example": "X-Session-Id=3f2b5d9f1d08440fa7a53fdff66fd4f9",
                "description": "Опциональная cookie текущей сессии",
            }
        ]
    },
)
def session_post(request: Request, sessions: SessionService = Depends(get_sessions)):
    now = utc_now_rfc3339()
    sid = extract_sid_cookie(request)
    if sid and sessions.exists(sid):
        try:
            sessions.refresh(sid, now)
        except (redis.RedisError, OSError):
            return resp_json(503, {"status": "redis_unavailable"})
        return resp_empty(200, cookie_refresh(sid, sessions.ttl))
    try:
        sid_new = sessions.create_anonymous_post_session(now)
    except (redis.RedisError, OSError):
        return resp_json(503, {"status": "redis_unavailable"})
    return resp_empty(201, cookie_refresh(sid_new, sessions.ttl))

