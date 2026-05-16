from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid4

import redis
from cassandra.query import BatchStatement, SimpleStatement
from redis.exceptions import ResponseError


def _event_title_hash(title: str) -> str:
    return hashlib.md5(title.encode("utf-8")).hexdigest()


def _cache_key(title: str) -> str:
    return f"event:{_event_title_hash(title)}:reviews"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ReviewsService:
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

    def create_review(self, *, event_id: str, title: str, created_by: str, comment: str, rating: int) -> UUID | None:
        now = _utc_now()
        rid = uuid4()
        insert_by_user = self._stmt(
            """
            INSERT INTO event_reviews_by_user (
                event_id, created_by, id, created_at, updated_at, rating, comment
            ) VALUES (%s, %s, %s, %s, %s, %s, %s) IF NOT EXISTS
            """
        )
        insert_by_event = self._stmt(
            """
            INSERT INTO event_reviews (
                event_id, created_at, id, created_by, updated_at, rating, comment
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
        )
        applied = self.cassandra.execute(insert_by_user, (event_id, created_by, rid, now, now, rating, comment)).one()
        if not applied or not bool(applied.applied):
            return None
        try:
            self.cassandra.execute(insert_by_event, (event_id, now, rid, created_by, now, rating, comment))
        except Exception:
            rollback_stmt = self._stmt("DELETE FROM event_reviews_by_user WHERE event_id = %s AND created_by = %s")
            self.cassandra.execute(rollback_stmt, (event_id, created_by))
            raise
        self._invalidate_title(title)
        return rid

    def list_reviews(self, *, event_id: str, limit: int | None, offset: int) -> list[dict]:
        if limit == 0:
            return []
        stmt = self._stmt(
            """
            SELECT id, event_id, rating, comment, created_at, created_by, updated_at
            FROM event_reviews
            WHERE event_id = %s
            """
        )
        rows = self.cassandra.execute(stmt, (event_id,))
        out: list[dict] = []
        to_skip = offset
        for row in rows:
            if to_skip > 0:
                to_skip -= 1
                continue
            out.append(
                {
                    "id": str(row.id),
                    "event_id": row.event_id,
                    "comment": row.comment or "",
                    "created_at": _to_rfc3339(row.created_at or _utc_now()),
                    "created_by": row.created_by,
                    "rating": int(row.rating),
                    "updated_at": _to_rfc3339(row.updated_at or row.created_at or _utc_now()),
                }
            )
            if limit is not None and len(out) >= limit:
                break
        return out

    def update_review(
        self,
        *,
        event_id: str,
        title: str,
        created_by: str,
        review_id: str,
        rating: int | None,
        comment: str | None,
    ) -> bool:
        row_stmt = self._stmt(
            """
            SELECT id, created_at, rating, comment
            FROM event_reviews_by_user
            WHERE event_id = %s AND created_by = %s
            """
        )
        row = self.cassandra.execute(row_stmt, (event_id, created_by)).one()
        if not row:
            return False
        if str(row.id) != review_id:
            return False

        new_rating = int(row.rating) if rating is None else rating
        new_comment = row.comment or "" if comment is None else comment
        now = _utc_now()
        batch = BatchStatement()
        update_by_user = self._stmt(
            """
            UPDATE event_reviews_by_user
            SET rating = %s, comment = %s, updated_at = %s
            WHERE event_id = %s AND created_by = %s
            """
        )
        update_by_event = self._stmt(
            """
            UPDATE event_reviews
            SET rating = %s, comment = %s, updated_at = %s
            WHERE event_id = %s AND created_at = %s AND id = %s
            """
        )
        batch.add(update_by_user, (new_rating, new_comment, now, event_id, created_by))
        batch.add(update_by_event, (new_rating, new_comment, now, event_id, row.created_at, row.id))
        self.cassandra.execute(batch)
        self._invalidate_title(title)
        return True

    def _stats_for_event_id(self, event_id: str) -> tuple[int, int]:
        stmt = self._stmt("SELECT rating FROM event_reviews WHERE event_id = %s")
        rows = self.cassandra.execute(stmt, (event_id,))
        count = 0
        rating_sum = 0
        for row in rows:
            count += 1
            rating_sum += int(row.rating)
        return count, rating_sum

    def get_reviews_for_title(self, *, title: str, event_ids: list[str]) -> dict:
        key = _cache_key(title)
        try:
            cached = self.r.hgetall(key)
        except ResponseError:
            self.r.delete(key)
            cached = {}

        if cached:
            count = int(cached.get("count") or 0)
            rating = float(cached.get("rating") or 0.0)
            return {"count": count, "rating": rating}

        total_count = 0
        total_sum = 0
        for event_id in event_ids:
            c, s = self._stats_for_event_id(event_id)
            total_count += c
            total_sum += s

        avg = 0.0 if total_count == 0 else round(total_sum / total_count, 1)
        payload = {"count": total_count, "rating": avg}
        pipe = self.r.pipeline()
        pipe.hset(key, mapping={"count": str(total_count), "rating": str(avg)})
        pipe.expire(key, self.cache_ttl)
        pipe.execute()
        return payload
