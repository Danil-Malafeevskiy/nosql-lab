from __future__ import annotations


def ensure_mongo_indexes(db) -> None:
    db.users.create_index("username", unique=True)
    db.events.create_index("title")
    try:
        db.events.drop_index("created_by_1_title_1")
    except Exception:
        pass
    db.events.create_index([("created_by", 1), ("title", 1)])
    db.events.create_index([("title", 1), ("created_by", 1)])
    db.events.create_index("created_by")
