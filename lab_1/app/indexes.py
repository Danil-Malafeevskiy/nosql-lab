from __future__ import annotations


def ensure_mongo_indexes(db) -> None:
    db.users.create_index("username", unique=True)
    db.events.create_index("title", unique=True)
    db.events.create_index([("title", 1), ("created_by", 1)])
    db.events.create_index("created_by")
