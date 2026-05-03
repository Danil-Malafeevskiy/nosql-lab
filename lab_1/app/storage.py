from __future__ import annotations

import redis

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

