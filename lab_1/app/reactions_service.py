from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import redis
from cassandra.query import BatchStatement, SimpleStatement


def _event_title_hash(title: str) -> str:
    return hashlib.md5(title.encode("utf-8")).hexdigest()


def _cache_key(title: str) -> str:
    return f"event:{_event_title_hash(title)}:reactions"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReactionsService:
    def __init__(self, cassandra_session, r: redis.Redis, *, cache_ttl: int, cassandra_consistency: str) -> None:
        self.cassandra = cassandra_session
        self.r = r
        self.cache_ttl = cache_ttl
        self.cassandra_consistency = cassandra_consistency

    def _stmt(self, query: str) -> SimpleStatement:
        cons = (self.cassandra_consistency or "").strip().upper()
        if cons == "ONE":
            from cassandra import ConsistencyLevel

            return SimpleStatement(query, consistency_level=ConsistencyLevel.ONE)
        if cons == "QUORUM":
            from cassandra import ConsistencyLevel

            return SimpleStatement(query, consistency_level=ConsistencyLevel.QUORUM)
        if cons == "ALL":
            from cassandra import ConsistencyLevel

            return SimpleStatement(query, consistency_level=ConsistencyLevel.ALL)
        return SimpleStatement(query)

    def _invalidate_title(self, title: str) -> None:
        self.r.delete(_cache_key(title))

    def set_like(self, *, event_id: str, title: str, user_id: str, like_value: int) -> None:
        if like_value not in (1, -1):
            raise ValueError("invalid like_value")

        now = _utc_now()
        other = -1 if like_value == 1 else 1

        delete_stmt = self._stmt(
            "DELETE FROM event_reactions WHERE event_id = %s AND like_value = %s AND created_by = %s"
        )
        insert_stmt = self._stmt(
            "INSERT INTO event_reactions (event_id, like_value, created_by, created_at) VALUES (%s, %s, %s, %s)"
        )
        batch = BatchStatement()
        batch.add(delete_stmt, (event_id, other, user_id))
        batch.add(insert_stmt, (event_id, like_value, user_id, now))
        self.cassandra.execute(batch)
        self._invalidate_title(title)

    def _count_for_event_id(self, event_id: str) -> tuple[int, int]:
        q = "SELECT COUNT(*) AS c FROM event_reactions WHERE event_id = %s AND like_value = %s"
        stmt = self._stmt(q)
        likes_row = self.cassandra.execute(stmt, (event_id, 1)).one()
        dislikes_row = self.cassandra.execute(stmt, (event_id, -1)).one()
        likes = int(likes_row.c) if likes_row and likes_row.c is not None else 0
        dislikes = int(dislikes_row.c) if dislikes_row and dislikes_row.c is not None else 0
        return likes, dislikes

    def get_reactions_for_title(self, *, title: str, event_ids: list[str]) -> dict:
        key = _cache_key(title)
        cached = self.r.get(key)
        if cached:
            try:
                d = json.loads(cached)
                likes = int(d.get("likes") or 0)
                dislikes = int(d.get("dislikes") or 0)
                return {"likes": likes, "dislikes": dislikes}
            except Exception:
                pass

        likes_total = 0
        dislikes_total = 0
        for eid in event_ids:
            l, d = self._count_for_event_id(eid)
            likes_total += l
            dislikes_total += d

        payload = {"likes": likes_total, "dislikes": dislikes_total}
        self.r.setex(key, self.cache_ttl, json.dumps(payload, separators=(",", ":")))
        return payload

