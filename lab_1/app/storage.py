from __future__ import annotations

import time
from urllib.parse import quote_plus

import redis
from cassandra.auth import PlainTextAuthProvider
from cassandra.cluster import Cluster, Session
from neo4j import Driver, GraphDatabase
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


def create_cassandra(s: Settings) -> Session:
    auth = None
    if s.cassandra_username or s.cassandra_password:
        auth = PlainTextAuthProvider(username=s.cassandra_username, password=s.cassandra_password)

    last_exc: Exception | None = None
    session: Session | None = None
    for _ in range(30):
        try:
            cluster = Cluster(
                contact_points=s.cassandra_hosts,
                port=s.cassandra_port,
                auth_provider=auth,
            )
            session = cluster.connect()
            session.execute("SELECT release_version FROM system.local")
            session.set_keyspace(s.cassandra_keyspace)
            last_exc = None
            break
        except Exception as e:
            last_exc = e
            time.sleep(2)
    if session is None:
        raise last_exc or RuntimeError("failed to connect to cassandra")
    return session


def create_neo4j(s: Settings) -> Driver:
    last_exc: Exception | None = None
    driver: Driver | None = None
    for _ in range(30):
        try:
            driver = GraphDatabase.driver(
                s.neo4j_url,
                auth=(s.neo4j_username, s.neo4j_password),
            )
            driver.verify_connectivity()
            last_exc = None
            break
        except Exception as e:
            last_exc = e
            time.sleep(2)
    if driver is None:
        raise last_exc or RuntimeError("failed to connect to neo4j")
    return driver
