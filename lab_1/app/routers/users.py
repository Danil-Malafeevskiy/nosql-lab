from __future__ import annotations

import json

import bcrypt
import redis
from fastapi import APIRouter, Depends, Request
from pymongo.errors import DuplicateKeyError

from ..deps import get_mongo, get_sessions
from ..http import cookie_refresh, extract_sid_cookie, resp_empty, resp_json
from ..routers.common import optional_session_refresh_set_cookie, redis_unavailable
from ..session_service import SessionService
from ..utils import utc_now_rfc3339


router = APIRouter()


@router.post("/users")
async def users_post(request: Request, mongo=Depends(get_mongo), sessions: SessionService = Depends(get_sessions)):
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

    for field in ("full_name", "username", "password"):
        v = data.get(field)
        if not isinstance(v, str) or not v.strip():
            return resp_json(400, {"message": f'invalid "{field}" field'}, set_cookie)

    full_name = data["full_name"].strip()
    username = data["username"].strip()
    password = data["password"]

    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    now = utc_now_rfc3339()

    try:
        result = mongo.users.insert_one({"full_name": full_name, "username": username, "password_hash": pw_hash})
    except DuplicateKeyError:
        return resp_json(409, {"message": "user already exists"}, set_cookie)
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)

    user_hex = str(result.inserted_id)
    try:
        if sid:
            sessions.delete(sid)
        new_sid = sessions.create_atomic(now, user_hex)
    except (redis.RedisError, OSError):
        try:
            mongo.users.delete_one({"_id": result.inserted_id})
        except Exception:
            pass
        return redis_unavailable()

    return resp_empty(201, cookie_refresh(new_sid, sessions.ttl))

