from __future__ import annotations

from urllib.parse import quote_plus

import redis
from pymongo import MongoClient
from pymongo.database import Database

from .settings import Settings


def create_redis(s: Settings) -> redis.Redis:
    r = redis.Redis(
        host=s.redis_host,
        port=s.redis_port,
        password=s.redis_password or None,
        db=s.redis_db,
        decode_responses=True,
    )
    r.ping()
    return r


def create_mongo(s: Settings) -> Database:
    u = quote_plus(s.mongodb_user)
    p = quote_plus(s.mongodb_password)
    host = s.mongodb_host
    port = s.mongodb_port
    db_name = s.mongodb_database

    extra = []
    if s.mongodb_auth_mechanism:
        extra.append(f"authMechanism={quote_plus(s.mongodb_auth_mechanism)}")

    a = quote_plus(db_name)
    uri = f"mongodb://{u}:{p}@{host}:{port}/{db_name}?authSource={a}"
    if extra:
        uri += "&" + "&".join(extra)

    mclient = MongoClient(uri, serverSelectionTimeoutMS=5000)
    db = mclient[db_name]
    db.command("ping")
    return db

