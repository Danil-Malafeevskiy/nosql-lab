from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import redis
from bson.objectid import ObjectId
from fastapi import APIRouter, Depends, Request
from pymongo.errors import DuplicateKeyError

from ..deps import get_mongo, get_sessions
from ..http import cookie_refresh, extract_sid_cookie, resp_empty, resp_json
from ..routers.common import optional_session_refresh_set_cookie, redis_unavailable
from ..session_service import SessionService
from ..utils import parse_rfc3339_datetime, utc_now_rfc3339


router = APIRouter()


@router.post("/events")
async def events_create(request: Request, mongo=Depends(get_mongo), sessions: SessionService = Depends(get_sessions)):
    raw = await request.body()
    sid = extract_sid_cookie(request)
    try:
        _sid_seen, set_cookie = optional_session_refresh_set_cookie(request, sessions)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    if not sid or not sessions.exists(sid):
        return resp_empty(401, set_cookie)

    user_id_hex = sessions.get_user_id(sid)
    if not user_id_hex:
        return resp_empty(401, set_cookie)
    try:
        ObjectId(user_id_hex)
    except Exception:
        return resp_empty(401, set_cookie)

    try:
        data = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return resp_json(400, {"message": 'invalid "body" field'}, set_cookie)
    if not isinstance(data, dict):
        return resp_json(400, {"message": 'invalid "body" field'}, set_cookie)

    title = data.get("title")
    address = data.get("address")
    description = data.get("description")
    started_at = data.get("started_at")
    finished_at = data.get("finished_at")

    for field, val in (
        ("title", title),
        ("address", address),
        ("description", description),
        ("started_at", started_at),
        ("finished_at", finished_at),
    ):
        if not isinstance(val, str) or not val.strip():
            return resp_json(400, {"message": f'invalid "{field}" field'}, set_cookie)

    _dt, err = parse_rfc3339_datetime(started_at)
    if err:
        return resp_json(400, {"message": 'invalid "started_at" field'}, set_cookie)
    _dt, err = parse_rfc3339_datetime(finished_at)
    if err:
        return resp_json(400, {"message": 'invalid "finished_at" field'}, set_cookie)

    created_str = utc_now_rfc3339()
    doc = {
        "title": title.strip(),
        "description": description.strip(),
        "location": {"address": address.strip()},
        "created_at": created_str,
        "created_by": user_id_hex,
        "started_at": started_at.strip(),
        "finished_at": finished_at.strip(),
    }

    try:
        ins = mongo.events.insert_one(doc)
    except DuplicateKeyError:
        return resp_json(409, {"message": "event already exists"}, set_cookie)
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)

    try:
        sessions.refresh(sid, utc_now_rfc3339())
        set_cookie2 = cookie_refresh(sid, sessions.ttl)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    return resp_json(201, {"id": str(ins.inserted_id)}, set_cookie2)


@router.get("/events")
def events_list(request: Request, mongo=Depends(get_mongo), sessions: SessionService = Depends(get_sessions)):
    sid = extract_sid_cookie(request)
    try:
        _sid_seen, set_cookie = optional_session_refresh_set_cookie(request, sessions)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    parsed = urlparse(str(request.url))
    qs = parse_qs(parsed.query, keep_blank_values=True)

    def parse_uint_param(name: str):
        if name not in qs or qs[name] == []:
            return None, True
        vals = qs[name]
        if len(vals) != 1:
            return None, False
        v = vals[0]
        if not re.fullmatch(r"[0-9]+", v):
            return None, False
        return int(v), True

    limit, lok = parse_uint_param("limit")
    offset, ook = parse_uint_param("offset")
    if not lok:
        return resp_json(400, {"message": 'invalid "limit" parameter'}, set_cookie)
    if not ook:
        return resp_json(400, {"message": 'invalid "offset" parameter'}, set_cookie)

    title_filter = None
    if "title" in qs and qs["title"]:
        if len(qs["title"]) != 1:
            return resp_json(400, {"message": 'invalid "title" parameter'}, set_cookie)
        title_filter = qs["title"][0]

    query = {}
    if title_filter is not None and title_filter != "":
        query["title"] = {"$regex": re.escape(title_filter), "$options": "i"}

    try:
        cursor = mongo.events.find(query).skip(offset or 0)
        if limit is not None:
            cursor = cursor.limit(limit)
        docs = list(cursor)
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)

    events_out = []
    for d in docs:
        events_out.append(
            {
                "id": str(d["_id"]),
                "title": d["title"],
                "description": d["description"],
                "location": d["location"],
                "created_at": d["created_at"],
                "created_by": d["created_by"],
                "started_at": d["started_at"],
                "finished_at": d["finished_at"],
            }
        )

    return resp_json(200, {"events": events_out, "count": len(events_out)}, set_cookie)

