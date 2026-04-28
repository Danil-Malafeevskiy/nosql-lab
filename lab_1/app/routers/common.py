from __future__ import annotations

import redis
from fastapi import Request

from ..http import cookie_refresh, extract_sid_cookie, resp_json
from ..session_service import SessionService
from ..utils import utc_now_rfc3339


def optional_session_refresh_set_cookie(request: Request, sessions: SessionService) -> tuple[str | None, str | None]:
    sid = extract_sid_cookie(request)
    if not sid:
        return None, None
    try:
        if not sessions.exists(sid):
            return sid, None
        sessions.refresh(sid, utc_now_rfc3339())
        return sid, cookie_refresh(sid, sessions.ttl)
    except (redis.RedisError, OSError):
        raise


def redis_unavailable():
    return resp_json(503, {"status": "redis_unavailable"})

