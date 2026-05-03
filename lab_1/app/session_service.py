from __future__ import annotations

import secrets

import redis


class SessionService:
    def __init__(self, r: redis.Redis, ttl: int) -> None:
        self.r = r
        self.ttl = ttl

    def _key(self, sid: str) -> str:
        return f"sid:{sid}"

    def exists(self, sid: str) -> bool:
        return bool(self.r.exists(self._key(sid)))

    def get_user_id(self, sid: str) -> str | None:
        uid = self.r.hget(self._key(sid), "user_id")
        return uid if uid else None

    def refresh(self, sid: str, now: str) -> None:
        key = self._key(sid)
        pipe = self.r.pipeline()
        pipe.hset(key, mapping={"updated_at": now})
        pipe.expire(key, self.ttl)
        pipe.execute()

    def bind_user(self, sid: str, user_id_hex: str, now: str) -> None:
        key = self._key(sid)
        pipe = self.r.pipeline()
        pipe.hset(key, mapping={"user_id": user_id_hex, "updated_at": now})
        pipe.expire(key, self.ttl)
        pipe.execute()

    def delete(self, sid: str) -> None:
        self.r.delete(self._key(sid))

    def create_atomic(self, now: str, user_id: str | None) -> str:
        script = """
        local key = KEYS[1]
        local created_at = ARGV[1]
        local updated_at = ARGV[2]
        local ttl = tonumber(ARGV[3])
        local user_id = ARGV[4]

        if redis.call('EXISTS', key) == 1 then
            return 0
        end
        redis.call('HSET', key, 'created_at', created_at, 'updated_at', updated_at)
        if user_id ~= '' then
            redis.call('HSET', key, 'user_id', user_id)
        end
        redis.call('EXPIRE', key, ttl)
        return 1
        """
        for _ in range(64):
            sid = secrets.token_hex(16)
            key = self._key(sid)
            uid = user_id or ""
            created = self.r.eval(script, 1, key, now, now, self.ttl, uid)
            if int(created) == 1:
                return sid
        raise RuntimeError("failed to allocate session id")

    def create_anonymous_post_session(self, now: str) -> str:
        script = """
        local key = KEYS[1]
        local created_at = ARGV[1]
        local updated_at = ARGV[2]
        local ttl = tonumber(ARGV[3])
        if redis.call('EXISTS', key) == 1 then
            return 0
        end
        redis.call('HSET', key, 'created_at', created_at, 'updated_at', updated_at)
        redis.call('EXPIRE', key, ttl)
        return 1
        """
        for _ in range(64):
            sid = secrets.token_hex(16)
            key = self._key(sid)
            created = self.r.eval(script, 1, key, now, now, self.ttl)
            if int(created) == 1:
                return sid
        raise RuntimeError("failed to allocate session id")

