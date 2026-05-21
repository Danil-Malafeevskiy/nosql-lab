CREATE KEYSPACE IF NOT EXISTS {{CASSANDRA_KEYSPACE}}
WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1};

USE {{CASSANDRA_KEYSPACE}};

CREATE TABLE IF NOT EXISTS event_reactions (
  event_id text,
  like_value tinyint,
  created_by text,
  created_at timestamp,
  PRIMARY KEY ((event_id, like_value), created_by)
);

CREATE INDEX IF NOT EXISTS ON event_reactions(created_by);
