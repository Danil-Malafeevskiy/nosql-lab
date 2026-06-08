from __future__ import annotations

import json
import re

import bcrypt
import redis
from fastapi import APIRouter, Depends, Request
from pymongo.errors import DuplicateKeyError

from ..deps import get_mongo, get_reactions, get_reviews, get_sessions
from ..http import cookie_refresh, extract_sid_cookie, resp_empty, resp_json
from ..routers.common import optional_session_refresh_set_cookie, redis_unavailable
from ..session_service import SessionService
from ..constants import OBJECT_ID_HEX
from ..utils import utc_now_rfc3339
from bson.objectid import ObjectId
from .events import _run_events_list_aggregation, _qs_map


router = APIRouter()


@router.post(
    "/users",
    summary="Регистрация пользователя",
    description="Введите `full_name`, `username`, `password`. При успехе вернется 201 и `Set-Cookie` с новой сессией.",
    responses={
        201: {"description": "Пользователь создан"}
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "example": {
                        "full_name": "Ivan Petrov",
                        "username": "ivan.petrov",
                        "password": "qwerty123",
                    }
                }
            },
        }
    },
)
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


@router.get(
    "/users",
    summary="Список пользователей",
    description="Можно передать фильтры `limit`, `offset`, `name`, `id`. Cookie не обязательна, но если есть, будет продлена.",
    responses={
        200: {
            "description": "Users list",
            "content": {
                "application/json": {
                    "example": {
                        "users": [
                            {
                                "id": "665f2f1d6f43a6a6f843cb11",
                                "full_name": "Ivan Petrov",
                                "username": "ivan.petrov",
                            }
                        ],
                        "count": 1,
                    }
                }
            },
        }
    },
    openapi_extra={
        "parameters": [
            {"name": "limit", "in": "query", "required": False, "schema": {"type": "integer"}, "example": 10},
            {"name": "offset", "in": "query", "required": False, "schema": {"type": "integer"}, "example": 0},
            {"name": "name", "in": "query", "required": False, "schema": {"type": "string"}, "example": "Ivan"},
            {
                "name": "id",
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
                "example": "665f2f1d6f43a6a6f843cb11",
            },
            {
                "name": "Cookie",
                "in": "header",
                "required": False,
                "schema": {"type": "string"},
                "example": "X-Session-Id=3f2b5d9f1d08440fa7a53fdff66fd4f9",
            },
        ]
    },
)
def users_list(request: Request, mongo=Depends(get_mongo), sessions: SessionService = Depends(get_sessions)):
    try:
        _sid_seen, set_cookie = optional_session_refresh_set_cookie(request, sessions)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    qs = request.query_params

    def getlist(name: str) -> list[str]:
        return qs.getlist(name)

    def parse_uint_field(name: str):
        vals = getlist(name)
        if not vals:
            return None, True
        if len(vals) != 1:
            return None, False
        v = vals[0]
        if not re.fullmatch(r"[0-9]+", v):
            return None, False
        return int(v), True

    limit, lok = parse_uint_field("limit")
    offset, ook = parse_uint_field("offset")
    if not lok:
        return resp_json(400, {"message": 'invalid "limit" field'}, set_cookie)
    if not ook:
        return resp_json(400, {"message": 'invalid "offset" field'}, set_cookie)

    def one_value(name: str):
        vals = getlist(name)
        if not vals:
            return None, True
        if len(vals) != 1:
            return None, False
        return vals[0], True

    name_q, nok = one_value("name")
    if not nok:
        return resp_json(400, {"message": 'invalid "name" field'}, set_cookie)
    id_q, iok = one_value("id")
    if not iok:
        return resp_json(400, {"message": 'invalid "id" field'}, set_cookie)

    qfilter: dict = {}
    if name_q is not None and name_q != "":
        qfilter["full_name"] = {"$regex": re.escape(name_q), "$options": "i"}
    if id_q is not None and id_q != "":
        if not OBJECT_ID_HEX.fullmatch(id_q):
            return resp_json(400, {"message": 'invalid "id" field'}, set_cookie)
        try:
            qfilter["_id"] = ObjectId(id_q)
        except Exception:
            return resp_json(400, {"message": 'invalid "id" field'}, set_cookie)

    try:
        cur = mongo.users.find(qfilter).skip(offset or 0)
        if limit is not None:
            cur = cur.limit(limit)
        users = list(cur)
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)

    out = [{"id": str(u["_id"]), "full_name": u["full_name"], "username": u["username"]} for u in users]
    return resp_json(200, {"users": out, "count": len(out)}, set_cookie)


@router.get(
    "/users/{uid}",
    summary="Получить пользователя по id",
    description="Укажите `uid` в path, например `665f2f1d6f43a6a6f843cb11`.",
    responses={
        200: {
            "description": "User details",
            "content": {
                "application/json": {
                    "example": {
                        "id": "665f2f1d6f43a6a6f843cb11",
                        "full_name": "Ivan Petrov",
                        "username": "ivan.petrov",
                    }
                }
            },
        }
    },
)
def users_get_one(uid: str, request: Request, mongo=Depends(get_mongo), sessions: SessionService = Depends(get_sessions)):
    try:
        _sid_seen, set_cookie = optional_session_refresh_set_cookie(request, sessions)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    try:
        oid = ObjectId(uid)
    except Exception:
        return resp_json(404, {"message": "Not found"}, set_cookie)

    try:
        u = mongo.users.find_one({"_id": oid})
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)
    if not u:
        return resp_json(404, {"message": "Not found"}, set_cookie)

    return resp_json(200, {"id": str(u["_id"]), "full_name": u["full_name"], "username": u["username"]}, set_cookie)


@router.get(
    "/users/{uid}/events",
    summary="События пользователя",
    description="Укажите `uid` в path. Можно использовать те же query-фильтры, что и в `/events`.",
    responses={
        200: {
            "description": "Events created by user",
            "content": {"application/json": {"example": {"events": [], "count": 0}}},
        }
    },
    openapi_extra={
        "parameters": [
            {
                "name": "include",
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
                "example": "reactions,reviews",
            },
            {"name": "limit", "in": "query", "required": False, "schema": {"type": "integer"}, "example": 10},
            {"name": "offset", "in": "query", "required": False, "schema": {"type": "integer"}, "example": 0},
        ]
    },
)
def users_events(
    uid: str,
    request: Request,
    mongo=Depends(get_mongo),
    reactions=Depends(get_reactions),
    reviews=Depends(get_reviews),
    sessions: SessionService = Depends(get_sessions),
):
    try:
        _sid_seen, set_cookie = optional_session_refresh_set_cookie(request, sessions)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    try:
        oid = ObjectId(uid)
    except Exception:
        return resp_json(404, {"message": "User not found"}, set_cookie)

    try:
        u = mongo.users.find_one({"_id": oid})
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)
    if not u:
        return resp_json(404, {"message": "User not found"}, set_cookie)

    qs = _qs_map(request)
    return _run_events_list_aggregation(
        mongo,
        reactions,
        reviews,
        qs,
        set_cookie,
        created_by_fixed=str(oid),
        include_reactions=False,
        include_reviews=False,
    )


