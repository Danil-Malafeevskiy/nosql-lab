from __future__ import annotations

import redis
from bson.objectid import ObjectId
from fastapi import APIRouter, Depends, Request

from ..deps import get_mongo, get_recommendations, get_sessions
from ..http import extract_sid_cookie, resp_empty, resp_json
from ..routers.common import optional_session_refresh_set_cookie, redis_unavailable
from ..session_service import SessionService


router = APIRouter()


def _event_to_dict(d: dict) -> dict:
    loc = dict(d.get("location") or {})
    loc_out = {"address": loc.get("address", "")}
    if loc.get("city"):
        loc_out["city"] = loc["city"]
    return {
        "id": str(d["_id"]),
        "title": d["title"],
        "category": d.get("category"),
        "price": int(d["price"]) if d.get("price") is not None else 0,
        "description": d["description"],
        "location": loc_out,
        "created_at": d["created_at"],
        "created_by": d["created_by"],
        "started_at": d["started_at"],
        "finished_at": d["finished_at"],
    }


def _load_events_by_ids(mongo, event_ids: list[str]) -> list[dict]:
    object_ids: list[ObjectId] = []
    for event_id in event_ids:
        try:
            object_ids.append(ObjectId(event_id))
        except Exception:
            continue
    if not object_ids:
        return []
    docs = list(mongo.events.find({"_id": {"$in": object_ids}}))
    by_id = {str(doc["_id"]): doc for doc in docs}
    return [by_id[event_id] for event_id in event_ids if event_id in by_id]


def _dedupe_by_title(docs: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen_titles: set[str] = set()
    for doc in docs:
        title = doc.get("title")
        if not isinstance(title, str):
            out.append(doc)
            continue
        if title in seen_titles:
            continue
        seen_titles.add(title)
        out.append(doc)
    return out


@router.get("/recommendations")
def recommendations_get(
    request: Request,
    mongo=Depends(get_mongo),
    recommendations=Depends(get_recommendations),
    sessions: SessionService = Depends(get_sessions),
):
    sid = extract_sid_cookie(request)
    try:
        _sid_seen, set_cookie = optional_session_refresh_set_cookie(request, sessions)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    if not sid or not sessions.exists(sid):
        return resp_empty(401)
    user_id = sessions.get_user_id(sid)
    if not user_id:
        return resp_empty(401)
    try:
        ObjectId(user_id)
    except Exception:
        return resp_empty(401)

    try:
        cached_events = recommendations.get_cached_events(user_id)
    except (redis.RedisError, OSError):
        return redis_unavailable()
    if cached_events is not None:
        return resp_json(200, {"events": cached_events}, set_cookie)

    try:
        recommended_ids = recommendations.get_recommended_event_ids(user_id)
        docs = _dedupe_by_title(_load_events_by_ids(mongo, recommended_ids))
        events = [_event_to_dict(doc) for doc in docs]
    except (redis.RedisError, OSError):
        return redis_unavailable()
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)

    try:
        recommendations.cache_events(user_id, events)
    except (redis.RedisError, OSError):
        return redis_unavailable()
    return resp_json(200, {"events": events}, set_cookie)
