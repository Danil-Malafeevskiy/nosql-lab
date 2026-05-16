from __future__ import annotations

import json
import re
from datetime import timedelta

import redis
from bson.objectid import ObjectId
from fastapi import APIRouter, Depends, Request
from pymongo.errors import DuplicateKeyError

from ..deps import get_mongo, get_reactions, get_reviews, get_sessions
from ..http import cookie_clear, cookie_refresh, extract_sid_cookie, resp_empty, resp_json
from ..routers.common import optional_session_refresh_set_cookie, redis_unavailable
from ..session_service import SessionService
from ..constants import EVENT_CATEGORIES, OBJECT_ID_HEX
from ..utils import parse_rfc3339_datetime, parse_yyyymmdd, utc_now_rfc3339


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


def _qs_map(request: Request) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for k, v in request.query_params.multi_items():
        out.setdefault(k, []).append(v)
    return out


def _one_query_value(qs: dict[str, list[str]], name: str) -> tuple[str | None, bool]:
    if name not in qs or not qs[name]:
        return None, True
    if len(qs[name]) != 1:
        return None, False
    return qs[name][0], True


def _parse_uint_field(qs: dict[str, list[str]], name: str) -> tuple[int | None, bool]:
    if name not in qs or not qs[name]:
        return None, True
    if len(qs[name]) != 1:
        return None, False
    v = qs[name][0]
    if not re.fullmatch(r"[0-9]+", v):
        return None, False
    return int(v), True


def _parse_include_flags(qs: dict[str, list[str]]) -> tuple[set[str], bool]:
    include, ok = _one_query_value(qs, "include")
    if not ok:
        return set(), False
    if include is None or include == "":
        return set(), True
    flags = set()
    allowed = {"reactions", "reviews"}
    parts = include.split(",")
    for part in parts:
        name = part.strip()
        if name == "" or name not in allowed:
            return set(), False
        flags.add(name)
    return flags, True


def _run_events_list_aggregation(
    mongo,
    reactions,
    reviews,
    qs: dict[str, list[str]],
    set_cookie: str | None,
    *,
    created_by_fixed: str | None,
    include_reactions: bool,
    include_reviews: bool,
):
    limit, lok = _parse_uint_field(qs, "limit")
    offset, ook = _parse_uint_field(qs, "offset")
    if not lok:
        return resp_json(400, {"message": 'invalid "limit" field'}, set_cookie)
    if not ook:
        return resp_json(400, {"message": 'invalid "offset" field'}, set_cookie)

    if "include" in qs:
        include_flags, inc_ok = _parse_include_flags(qs)
        if not inc_ok:
            return resp_json(400, {"message": 'invalid "include" field'}, set_cookie)
        include_reactions = "reactions" in include_flags
        include_reviews = "reviews" in include_flags

    title_filter, tok = _one_query_value(qs, "title")
    if not tok:
        return resp_json(400, {"message": 'invalid "title" field'}, set_cookie)

    event_id_str, iok = _one_query_value(qs, "id")
    if not iok:
        return resp_json(400, {"message": 'invalid "id" field'}, set_cookie)
    event_oid = None
    if event_id_str is not None and event_id_str != "":
        if not OBJECT_ID_HEX.fullmatch(event_id_str):
            return resp_json(400, {"message": 'invalid "id" field'}, set_cookie)
        try:
            event_oid = ObjectId(event_id_str)
        except Exception:
            return resp_json(400, {"message": 'invalid "id" field'}, set_cookie)

    category, cok = _one_query_value(qs, "category")
    if not cok:
        return resp_json(400, {"message": 'invalid "category" field'}, set_cookie)
    if category is not None and category != "" and category not in EVENT_CATEGORIES:
        return resp_json(400, {"message": 'invalid "category" field'}, set_cookie)

    price_from, pf_ok = _parse_uint_field(qs, "price_from")
    price_to, pt_ok = _parse_uint_field(qs, "price_to")
    if not pf_ok:
        return resp_json(400, {"message": 'invalid "price_from" field'}, set_cookie)
    if not pt_ok:
        return resp_json(400, {"message": 'invalid "price_to" field'}, set_cookie)

    city, ciok = _one_query_value(qs, "city")
    if not ciok:
        return resp_json(400, {"message": 'invalid "city" field'}, set_cookie)

    df_raw, df_ok = _one_query_value(qs, "date_from")
    if not df_ok:
        return resp_json(400, {"message": 'invalid "date_from" field'}, set_cookie)
    dt_raw, dt_ok = _one_query_value(qs, "date_to")
    if not dt_ok:
        return resp_json(400, {"message": 'invalid "date_to" field'}, set_cookie)
    sdf_raw, sdf_ok = _one_query_value(qs, "started_date_from")
    if not sdf_ok:
        return resp_json(400, {"message": 'invalid "started_date_from" field'}, set_cookie)
    sdt_raw, sdt_ok = _one_query_value(qs, "started_date_to")
    if not sdt_ok:
        return resp_json(400, {"message": 'invalid "started_date_to" field'}, set_cookie)

    date_from_s = df_raw or sdf_raw
    date_to_s = dt_raw or sdt_raw
    date_from_dt = parse_yyyymmdd(date_from_s) if date_from_s else None
    date_to_dt = parse_yyyymmdd(date_to_s) if date_to_s else None
    if date_from_s and date_from_dt is None:
        fn = "date_from" if df_raw else "started_date_from"
        return resp_json(400, {"message": f'invalid "{fn}" field'}, set_cookie)
    if date_to_s and date_to_dt is None:
        fn = "date_to" if dt_raw else "started_date_to"
        return resp_json(400, {"message": f'invalid "{fn}" field'}, set_cookie)

    user_name, uok = _one_query_value(qs, "user")
    if not uok:
        return resp_json(400, {"message": 'invalid "user" field'}, set_cookie)

    match_pre: dict = {}
    if title_filter:
        match_pre["title"] = {"$regex": re.escape(title_filter), "$options": "i"}
    if category:
        match_pre["category"] = category
    if city is not None and city != "":
        match_pre["location.city"] = city
    if event_oid is not None:
        match_pre["_id"] = event_oid

    if created_by_fixed is not None:
        if user_name is not None and user_name != "":
            try:
                org_u = mongo.users.find_one({"username": user_name})
            except Exception:
                return resp_json(503, {"message": "database error"}, set_cookie)
            if not org_u or str(org_u["_id"]) != created_by_fixed:
                return resp_json(200, {"events": [], "count": 0}, set_cookie)
        match_pre["created_by"] = created_by_fixed
    elif user_name is not None and user_name != "":
        try:
            org = mongo.users.find_one({"username": user_name})
        except Exception:
            return resp_json(503, {"message": "database error"}, set_cookie)
        if not org:
            return resp_json(200, {"events": [], "count": 0}, set_cookie)
        match_pre["created_by"] = str(org["_id"])

    pipeline: list = []
    if match_pre:
        pipeline.append({"$match": match_pre})

    pipeline.append(
        {
            "$addFields": {
                "_eff_price": {"$ifNull": ["$price", 0]},
                "_start_dt": {
                    "$convert": {"input": "$started_at", "to": "date", "onError": None, "onNull": None}
                },
            }
        }
    )

    match_post: dict = {}
    if price_from is not None or price_to is not None:
        pr = {}
        if price_from is not None:
            pr["$gte"] = price_from
        if price_to is not None:
            pr["$lte"] = price_to
        match_post["_eff_price"] = pr
    if date_from_dt or date_to_dt:
        dr = {"$ne": None}
        if date_from_dt:
            dr["$gte"] = date_from_dt
        if date_to_dt:
            dr["$lte"] = date_to_dt + timedelta(days=1) - timedelta(microseconds=1)
        match_post["_start_dt"] = dr
    if match_post:
        pipeline.append({"$match": match_post})

    pipeline.append({"$project": {"_eff_price": 0, "_start_dt": 0}})

    skip_n = offset or 0
    if limit is not None:
        pipeline.extend([{"$skip": skip_n}, {"$limit": limit}])
    else:
        pipeline.append({"$skip": skip_n})

    try:
        docs = list(mongo.events.aggregate(pipeline))
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)

    events_out = [_event_to_dict(d) for d in docs]

    if include_reactions or include_reviews:
        titles = sorted({e["title"] for e in events_out if isinstance(e.get("title"), str) and e["title"] != ""})
        ids_by_title: dict[str, list[str]] = {}
        try:
            for t in titles:
                ids_by_title[t] = [str(d["_id"]) for d in mongo.events.find({"title": t}, {"_id": 1})]
        except Exception:
            return resp_json(503, {"message": "database error"}, set_cookie)

        reactions_by_title: dict[str, dict] = {}
        reviews_by_title: dict[str, dict] = {}
        try:
            if include_reactions:
                for t in titles:
                    reactions_by_title[t] = reactions.get_reactions_for_title(title=t, event_ids=ids_by_title[t])
            if include_reviews:
                for t in titles:
                    reviews_by_title[t] = reviews.get_reviews_for_title(title=t, event_ids=ids_by_title[t])
        except Exception:
            return resp_json(503, {"message": "database error"}, set_cookie)

        for e in events_out:
            if include_reactions:
                r = reactions_by_title.get(e["title"])
                e["reactions"] = r if r is not None else {"likes": 0, "dislikes": 0}
            if include_reviews:
                rv = reviews_by_title.get(e["title"])
                e["reviews"] = rv if rv is not None else {"count": 0, "rating": 0.0}
    return resp_json(200, {"events": events_out, "count": len(events_out)}, set_cookie)


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
        "price": 0,
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
def events_list(
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
    qs = _qs_map(request)
    return _run_events_list_aggregation(
        mongo,
        reactions,
        reviews,
        qs,
        set_cookie,
        created_by_fixed=None,
        include_reactions=False,
        include_reviews=False,
    )


@router.get("/events/{eid}")
def event_get_one(
    eid: str,
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
        oid = ObjectId(eid)
    except Exception:
        return resp_json(404, {"message": "Not found"}, set_cookie)
    try:
        doc = mongo.events.find_one({"_id": oid})
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)
    if not doc:
        return resp_json(404, {"message": "Not found"}, set_cookie)
    out = _event_to_dict(doc)
    qs = _qs_map(request)
    include_flags, ok = _parse_include_flags(qs)
    if not ok:
        return resp_json(400, {"message": 'invalid "include" field'}, set_cookie)
    include_reactions = "reactions" in include_flags
    include_reviews = "reviews" in include_flags
    if include_reactions or include_reviews:
        try:
            ids = [str(d["_id"]) for d in mongo.events.find({"title": out["title"]}, {"_id": 1})]
            if include_reactions:
                out["reactions"] = reactions.get_reactions_for_title(title=out["title"], event_ids=ids)
            if include_reviews:
                out["reviews"] = reviews.get_reviews_for_title(title=out["title"], event_ids=ids)
        except Exception:
            return resp_json(503, {"message": "database error"}, set_cookie)
    return resp_json(200, out, set_cookie)


def _parse_review_body(raw: bytes, set_cookie: str | None):
    try:
        data = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, resp_json(400, {"message": 'invalid "body" field'}, set_cookie)
    if not isinstance(data, dict):
        return None, resp_json(400, {"message": 'invalid "body" field'}, set_cookie)
    return data, None


def _parse_rating(value, *, required: bool, provided: bool, set_cookie: str | None):
    if not provided:
        if required:
            return None, resp_json(400, {"message": 'invalid "rating" field'}, set_cookie)
        return None, None
    if isinstance(value, bool):
        return None, resp_json(400, {"message": 'invalid "rating" field'}, set_cookie)
    if not isinstance(value, int):
        return None, resp_json(400, {"message": 'invalid "rating" field'}, set_cookie)
    if value < 1 or value > 5:
        return None, resp_json(400, {"message": 'invalid "rating" field'}, set_cookie)
    return value, None


def _parse_comment(value, *, required: bool, provided: bool, set_cookie: str | None):
    if not provided:
        if required:
            return None, resp_json(400, {"message": 'invalid "comment" field'}, set_cookie)
        return None, None
    if not isinstance(value, str):
        return None, resp_json(400, {"message": 'invalid "comment" field'}, set_cookie)
    if len(value) > 300:
        return None, resp_json(400, {"message": 'invalid "comment" field'}, set_cookie)
    return value, None


@router.post("/events/{eid}/reviews")
async def event_review_create(
    eid: str,
    request: Request,
    mongo=Depends(get_mongo),
    reviews=Depends(get_reviews),
    sessions: SessionService = Depends(get_sessions),
):
    raw = await request.body()
    sid = extract_sid_cookie(request)
    try:
        _sid_seen, set_cookie = optional_session_refresh_set_cookie(request, sessions)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    if not sid or not sessions.exists(sid):
        return resp_empty(401)
    uid = sessions.get_user_id(sid)
    if not uid:
        return resp_empty(401)
    try:
        ObjectId(uid)
    except Exception:
        return resp_empty(401)

    try:
        oid = ObjectId(eid)
    except Exception:
        return resp_json(404, {"message": "Event not found"}, set_cookie)

    data, err_resp = _parse_review_body(raw, set_cookie)
    if err_resp is not None:
        return err_resp

    rating, err_resp = _parse_rating(
        data.get("rating"), required=True, provided="rating" in data, set_cookie=set_cookie
    )
    if err_resp is not None:
        return err_resp
    comment, err_resp = _parse_comment(
        data.get("comment"), required=True, provided="comment" in data, set_cookie=set_cookie
    )
    if err_resp is not None:
        return err_resp

    try:
        doc = mongo.events.find_one({"_id": oid})
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)
    if not doc:
        return resp_json(404, {"message": "Event not found"}, set_cookie)

    try:
        rid = reviews.create_review(event_id=str(oid), title=doc["title"], created_by=uid, comment=comment, rating=rating)
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)
    if rid is None:
        return resp_json(409, {"message": "Already exists"}, set_cookie)

    try:
        sessions.refresh(sid, utc_now_rfc3339())
        set_cookie2 = cookie_refresh(sid, sessions.ttl)
    except (redis.RedisError, OSError):
        return redis_unavailable()
    return resp_json(201, {"id": str(rid)}, set_cookie2)


@router.get("/events/{eid}/reviews")
def event_reviews_list(
    eid: str,
    request: Request,
    mongo=Depends(get_mongo),
    reviews=Depends(get_reviews),
    sessions: SessionService = Depends(get_sessions),
):
    try:
        _sid_seen, set_cookie = optional_session_refresh_set_cookie(request, sessions)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    qs = _qs_map(request)
    limit, lok = _parse_uint_field(qs, "limit")
    offset, ook = _parse_uint_field(qs, "offset")
    if not lok:
        return resp_json(400, {"message": 'invalid "limit" field'}, set_cookie)
    if not ook:
        return resp_json(400, {"message": 'invalid "offset" field'}, set_cookie)

    try:
        oid = ObjectId(eid)
    except Exception:
        return resp_json(404, {"message": "Event not found"}, set_cookie)
    try:
        doc = mongo.events.find_one({"_id": oid}, {"_id": 1})
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)
    if not doc:
        return resp_json(404, {"message": "Event not found"}, set_cookie)

    try:
        result = reviews.list_reviews(event_id=str(oid), limit=limit, offset=offset or 0)
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)
    return resp_json(200, {"reviews": result, "count": len(result)}, set_cookie)


@router.patch("/events/{eid}/reviews/{rid}")
async def event_review_patch(
    eid: str,
    rid: str,
    request: Request,
    mongo=Depends(get_mongo),
    reviews=Depends(get_reviews),
    sessions: SessionService = Depends(get_sessions),
):
    raw = await request.body()
    sid = extract_sid_cookie(request)
    try:
        _sid_seen, set_cookie = optional_session_refresh_set_cookie(request, sessions)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    if not sid or not sessions.exists(sid):
        return resp_empty(401)
    uid = sessions.get_user_id(sid)
    if not uid:
        return resp_empty(401)
    try:
        ObjectId(uid)
    except Exception:
        return resp_empty(401)

    data, err_resp = _parse_review_body(raw, set_cookie)
    if err_resp is not None:
        return err_resp

    rating, err_resp = _parse_rating(
        data.get("rating"), required=False, provided="rating" in data, set_cookie=set_cookie
    )
    if err_resp is not None:
        return err_resp
    comment, err_resp = _parse_comment(
        data.get("comment"), required=False, provided="comment" in data, set_cookie=set_cookie
    )
    if err_resp is not None:
        return err_resp
    if "rating" not in data and "comment" not in data:
        return resp_json(400, {"message": 'invalid "body" field'}, set_cookie)

    try:
        oid = ObjectId(eid)
    except Exception:
        return resp_json(404, {"message": "Event not found"}, set_cookie)
    try:
        doc = mongo.events.find_one({"_id": oid}, {"title": 1})
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)
    if not doc:
        return resp_json(404, {"message": "Event not found"}, set_cookie)

    try:
        ok = reviews.update_review(
            event_id=str(oid),
            title=doc["title"],
            created_by=uid,
            review_id=rid,
            rating=rating,
            comment=comment,
        )
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)
    if not ok:
        return resp_json(404, {"message": "Event not found"}, set_cookie)

    try:
        sessions.refresh(sid, utc_now_rfc3339())
        set_cookie2 = cookie_refresh(sid, sessions.ttl)
    except (redis.RedisError, OSError):
        return redis_unavailable()
    return resp_empty(204, set_cookie2)


@router.post("/events/{eid}/like")
def event_like(
    eid: str,
    request: Request,
    mongo=Depends(get_mongo),
    reactions=Depends(get_reactions),
    sessions: SessionService = Depends(get_sessions),
):
    _ = request
    sid = extract_sid_cookie(request)
    try:
        _sid_seen, set_cookie = optional_session_refresh_set_cookie(request, sessions)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    if not sid or not sessions.exists(sid):
        return resp_empty(401)
    uid = sessions.get_user_id(sid)
    if not uid:
        return resp_empty(401)
    try:
        ObjectId(uid)
    except Exception:
        return resp_empty(401)

    try:
        oid = ObjectId(eid)
    except Exception:
        return resp_json(404, {"message": "Event not found"}, set_cookie)

    try:
        doc = mongo.events.find_one({"_id": oid})
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)
    if not doc:
        return resp_json(404, {"message": "Event not found"}, set_cookie)

    try:
        reactions.set_like(event_id=str(oid), title=doc["title"], user_id=uid, like_value=1)
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)

    try:
        ids = [str(d["_id"]) for d in mongo.events.find({"title": doc["title"]}, {"_id": 1})]
        reactions.get_reactions_for_title(title=doc["title"], event_ids=ids)
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)

    try:
        sessions.refresh(sid, utc_now_rfc3339())
        set_cookie2 = cookie_refresh(sid, sessions.ttl)
    except (redis.RedisError, OSError):
        return redis_unavailable()
    return resp_empty(204, set_cookie2)


@router.post("/events/{eid}/dislike")
def event_dislike(
    eid: str,
    request: Request,
    mongo=Depends(get_mongo),
    reactions=Depends(get_reactions),
    sessions: SessionService = Depends(get_sessions),
):
    _ = request
    sid = extract_sid_cookie(request)
    try:
        _sid_seen, set_cookie = optional_session_refresh_set_cookie(request, sessions)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    if not sid or not sessions.exists(sid):
        return resp_empty(401, cookie_clear())
    uid = sessions.get_user_id(sid)
    if not uid:
        return resp_empty(401, cookie_clear())
    try:
        ObjectId(uid)
    except Exception:
        return resp_empty(401, cookie_clear())

    try:
        oid = ObjectId(eid)
    except Exception:
        return resp_json(404, {"message": "Event not found"}, set_cookie)

    try:
        doc = mongo.events.find_one({"_id": oid})
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)
    if not doc:
        return resp_json(404, {"message": "Event not found"}, set_cookie)

    try:
        reactions.set_like(event_id=str(oid), title=doc["title"], user_id=uid, like_value=-1)
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)

    try:
        ids = [str(d["_id"]) for d in mongo.events.find({"title": doc["title"]}, {"_id": 1})]
        reactions.get_reactions_for_title(title=doc["title"], event_ids=ids)
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)

    try:
        sessions.refresh(sid, utc_now_rfc3339())
        set_cookie2 = cookie_refresh(sid, sessions.ttl)
    except (redis.RedisError, OSError):
        return redis_unavailable()
    return resp_empty(204, set_cookie2)


@router.patch("/events/{eid}")
async def event_patch(eid: str, request: Request, mongo=Depends(get_mongo), sessions: SessionService = Depends(get_sessions)):
    raw = await request.body()
    sid = extract_sid_cookie(request)
    try:
        _sid_seen, set_cookie = optional_session_refresh_set_cookie(request, sessions)
    except (redis.RedisError, OSError):
        return redis_unavailable()

    if not sid or not sessions.exists(sid):
        return resp_empty(401, set_cookie)
    uid = sessions.get_user_id(sid)
    if not uid:
        return resp_empty(401, set_cookie)

    try:
        oid = ObjectId(eid)
    except Exception:
        return resp_json(
            404,
            {"message": "Not found. Be sure that event exists and you are the organizer"},
            set_cookie,
        )

    if not raw:
        data = {}
    else:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return resp_json(400, {"message": 'invalid "body" field'}, set_cookie)
    if not isinstance(data, dict):
        return resp_json(400, {"message": 'invalid "body" field'}, set_cookie)

    if "category" in data:
        v = data["category"]
        if not isinstance(v, str) or v not in EVENT_CATEGORIES:
            return resp_json(400, {"message": 'invalid "category" field'}, set_cookie)
    if "price" in data:
        p = data["price"]
        if isinstance(p, bool):
            return resp_json(400, {"message": 'invalid "price" field'}, set_cookie)
        if isinstance(p, float):
            if p < 0 or not p.is_integer():
                return resp_json(400, {"message": 'invalid "price" field'}, set_cookie)
            p = int(p)
        elif isinstance(p, int):
            if p < 0:
                return resp_json(400, {"message": 'invalid "price" field'}, set_cookie)
        else:
            return resp_json(400, {"message": 'invalid "price" field'}, set_cookie)
        data = dict(data)
        data["price"] = p
    if "city" in data:
        if not isinstance(data["city"], str):
            return resp_json(400, {"message": 'invalid "city" field'}, set_cookie)

    try:
        doc = mongo.events.find_one({"_id": oid})
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)

    nf = {"message": "Not found. Be sure that event exists and you are the organizer"}
    if not doc or doc.get("created_by") != uid:
        return resp_json(404, nf, set_cookie)

    loc = dict(doc.get("location") or {})
    if "address" not in loc:
        loc["address"] = ""

    set_doc: dict = {}
    if "category" in data:
        set_doc["category"] = data["category"]
    if "price" in data:
        set_doc["price"] = data["price"]
    if "city" in data:
        if data["city"] == "":
            loc.pop("city", None)
        else:
            loc["city"] = data["city"].strip()
        set_doc["location"] = loc

    if not set_doc:
        try:
            sessions.refresh(sid, utc_now_rfc3339())
            set_cookie2 = cookie_refresh(sid, sessions.ttl)
        except (redis.RedisError, OSError):
            return redis_unavailable()
        return resp_empty(204, set_cookie2)

    try:
        mongo.events.update_one({"_id": oid}, {"$set": set_doc})
    except Exception:
        return resp_json(503, {"message": "database error"}, set_cookie)

    try:
        sessions.refresh(sid, utc_now_rfc3339())
        set_cookie2 = cookie_refresh(sid, sessions.ttl)
    except (redis.RedisError, OSError):
        return redis_unavailable()
    return resp_empty(204, set_cookie2)

