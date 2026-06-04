from __future__ import annotations

import json

import redis
from neo4j import Driver
from redis.exceptions import ResponseError


class RecommendationsService:
    def __init__(self, driver: Driver, r: redis.Redis, *, cache_ttl: int) -> None:
        self.driver = driver
        self.r = r
        self.cache_ttl = cache_ttl

    def _cache_key(self, user_id: str) -> str:
        return f"user:{user_id}:recomms"

    def invalidate_user_cache(self, user_id: str) -> None:
        self.r.delete(self._cache_key(user_id))

    def add_liked_event(self, *, user_id: str, event_id: str, title: str) -> None:
        query = """
        MERGE (u:User {id: $user_id})
        MERGE (e:Event {id: $event_id})
        SET e.title = $title
        MERGE (u)-[:LIKED]->(e)
        """
        with self.driver.session() as session:
            session.run(
                query,
                user_id=user_id,
                event_id=event_id,
                title=title,
            ).consume()
        self.invalidate_user_cache(user_id)

    def get_cached_events(self, user_id: str) -> list[dict] | None:
        key = self._cache_key(user_id)
        try:
            cached = self.r.hgetall(key)
        except ResponseError:
            self.r.delete(key)
            return None
        if not cached:
            return None
        raw_events = cached.get("events")
        if not raw_events:
            return None
        try:
            events = json.loads(raw_events)
        except json.JSONDecodeError:
            self.r.delete(key)
            return None
        if not isinstance(events, list):
            self.r.delete(key)
            return None
        return events

    def cache_events(self, user_id: str, events: list[dict]) -> None:
        key = self._cache_key(user_id)
        payload = json.dumps(events)
        pipe = self.r.pipeline()
        pipe.hset(key, mapping={"events": payload})
        pipe.expire(key, self.cache_ttl)
        pipe.execute()

    def get_recommended_event_ids(self, user_id: str) -> list[str]:
        query = """
        MATCH (u:User {id: $user_id})-[:LIKED]->(:Event)<-[:LIKED]-(other:User)-[:LIKED]->(rec:Event)
        WHERE NOT (u)-[:LIKED]->(rec)
        WITH rec, count(DISTINCT other) AS score
        ORDER BY score DESC, rec.id ASC
        RETURN rec.id AS id
        """
        with self.driver.session() as session:
            rows = session.run(query, user_id=user_id)
            return [record["id"] for record in rows if isinstance(record.get("id"), str) and record["id"]]
