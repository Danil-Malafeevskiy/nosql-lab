#!/bin/bash
set -euo pipefail
sleep 10

wait_for_mongo() {
  local host="$1"
  for _ in $(seq 1 60); do
    if mongosh "mongodb://${host}:${MONGODB_PORT}" --quiet --eval "db.runCommand({ping:1}).ok" 2>/dev/null | grep -q 1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_mongo "mongo-cfg-1"
mongosh "mongodb://mongo-cfg-1:${MONGODB_PORT}" --quiet <<JS
try {
  rs.status();
} catch (e) {
  rs.initiate({
    _id: "cfgrepl",
    configsvr: true,
    members: [
      { _id: 0, host: "mongo-cfg-1:${MONGODB_PORT}" },
      { _id: 1, host: "mongo-cfg-2:${MONGODB_PORT}" },
      { _id: 2, host: "mongo-cfg-3:${MONGODB_PORT}" }
    ]
  });
}
JS

echo "Waiting for config RS..."
for _ in $(seq 1 120); do
  if mongosh "mongodb://mongo-cfg-1:${MONGODB_PORT}" --quiet --eval "try{rs.status().ok}catch(e){0}" 2>/dev/null | grep -q 1; then
    break
  fi
  sleep 2
done

wait_for_mongo "mongo-shard-1"
mongosh "mongodb://mongo-shard-1:${MONGODB_PORT}" --quiet <<JS
try {
  rs.status();
} catch (e) {
  rs.initiate({
    _id: "shardrepl",
    members: [
      { _id: 0, host: "mongo-shard-1:${MONGODB_PORT}" },
      { _id: 1, host: "mongo-shard-2:${MONGODB_PORT}" },
      { _id: 2, host: "mongo-shard-3:${MONGODB_PORT}" }
    ]
  });
}
JS

echo "Waiting for shard RS..."
for _ in $(seq 1 120); do
  if mongosh "mongodb://mongo-shard-1:${MONGODB_PORT}" --quiet --eval "try{rs.status().ok}catch(e){0}" 2>/dev/null | grep -q 1; then
    break
  fi
  sleep 2
done

wait_for_mongo "mongo-shard2-1"
mongosh "mongodb://mongo-shard2-1:${MONGODB_PORT}" --quiet <<JS
try {
  rs.status();
} catch (e) {
  rs.initiate({
    _id: "shardrepl2",
    members: [
      { _id: 0, host: "mongo-shard2-1:${MONGODB_PORT}" },
      { _id: 1, host: "mongo-shard2-2:${MONGODB_PORT}" },
      { _id: 2, host: "mongo-shard2-3:${MONGODB_PORT}" }
    ]
  });
}
JS

echo "Waiting for shard2 RS..."
for _ in $(seq 1 120); do
  if mongosh "mongodb://mongo-shard2-1:${MONGODB_PORT}" --quiet --eval "try{rs.status().ok}catch(e){0}" 2>/dev/null | grep -q 1; then
    break
  fi
  sleep 2
done

echo "Waiting for mongos..."
for _ in $(seq 1 240); do
  if mongosh "mongodb://mongos:${MONGODB_PORT}" --quiet --eval "db.runCommand({ping:1}).ok" 2>/dev/null | grep -q 1; then
    break
  fi
  sleep 2
done

mongosh "mongodb://mongos:${MONGODB_PORT}" --quiet <<JS
try {
  sh.addShard("shardrepl/mongo-shard-1:${MONGODB_PORT},mongo-shard-2:${MONGODB_PORT},mongo-shard-3:${MONGODB_PORT}");
} catch (e) {}

try {
  sh.addShard("shardrepl2/mongo-shard2-1:${MONGODB_PORT},mongo-shard2-2:${MONGODB_PORT},mongo-shard2-3:${MONGODB_PORT}");
} catch (e) {}

const eh = db.getSiblingDB("eventhub");
try {
  eh.createUser({
    user: "eventhub_app",
    pwd: "eventhub_secret",
    roles: [{ role: "readWrite", db: "eventhub" }]
  });
} catch (e) {}

try {
  sh.enableSharding("eventhub");
} catch (e) {}

try {
  eh.createCollection("events");
} catch (e) {}

try {
  sh.shardCollection("eventhub.events", { created_by: "hashed" });
} catch (e) {}
JS

echo "Sharding setup done."
