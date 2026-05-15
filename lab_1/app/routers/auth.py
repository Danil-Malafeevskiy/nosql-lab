from __future__ import annotations

import json

import bcrypt
import redis
from fastapi import APIRouter, Depends, Request

from ..deps import get_mongo, get_sessions
from ..http import cookie_clear, cookie_refresh, extract_sid_cookie, resp_empty, resp_json
from ..routers.common import optional_session_refresh_set_cookie, redis_unavailable
from ..session_service import SessionService
from ..utils import utc_now_rfc3339


router = APIRouter()


@router.post("/auth/login")
async def auth_login(request: Request, mongo=Depends(get_mongo), sessions: SessionService = Depends(get_sessions)):
    raw = await request.body()
    sid = extract_sid_cookie(request)
    try:
        _sid_seen, set_cookie = optional_session_refresh_set_cookie(request, sessions)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    try:
        data = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return resp_json(400, {"message": 'invalid "body" field'}, set_cookie)

    if not isinstance(data, dict):
        return resp_json(400, {"message": 'invalid "body" field'}, set_cookie)

    for field in ("username", "password"):
        v = data.get(field)
        if not isinstance(v, str) or not v.strip():
            return resp_json(400, {"message": f'invalid "{field}" field'}, set_cookie)

    username = data["username"].strip()
    password = data["password"]

    try:
        user = mongo.users.find_one({"username": username})
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)

    ok = bool(user) and bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8"))
    now = utc_now_rfc3339()
    user_hex = str(user["_id"]) if user else ""

    if not ok:
        return resp_json(401, {"message": "invalid credentials"}, set_cookie)

    try:
        if sid and sessions.exists(sid):
            sessions.bind_user(sid, user_hex, now)
            out_sid = sid
        else:
            out_sid = sessions.create_atomic(now, user_hex)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    return resp_empty(204, cookie_refresh(out_sid, sessions.ttl))


@router.post("/auth/logout")
async def auth_logout(request: Request, sessions: SessionService = Depends(get_sessions)):
    _ = await request.body()
    sid = extract_sid_cookie(request)
    try:
        _sid_seen, set_cookie = optional_session_refresh_set_cookie(request, sessions)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    if not sid or not sessions.exists(sid):
        return resp_empty(401, set_cookie)

    if not sessions.get_user_id(sid):
        return resp_empty(401, set_cookie)

    try:
        sessions.delete(sid)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    return resp_empty(204, cookie_clear())

