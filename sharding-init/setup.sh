#!/bin/bash
set -euo pipefail
sleep 10

mongosh "mongodb://mongo-cfg-1:27017" --quiet <<'JS'
try {
  rs.status();
} catch (e) {
  rs.initiate({
    _id: "cfgrepl",
    configsvr: true,
    members: [
      { _id: 0, host: "mongo-cfg-1:27017" },
      { _id: 1, host: "mongo-cfg-2:27017" },
      { _id: 2, host: "mongo-cfg-3:27017" }
    ]
  });
}
JS

echo "Waiting for config RS..."
for _ in $(seq 1 60); do
  if mongosh "mongodb://mongo-cfg-1:27017" --quiet --eval "try{rs.status().ok}catch(e){0}" 2>/dev/null | grep -q true; then
    break
  fi
  sleep 2
done

mongosh "mongodb://mongo-shard-1:27017" --quiet <<'JS'
try {
  rs.status();
} catch (e) {
  rs.initiate({
    _id: "shardrepl",
    members: [
      { _id: 0, host: "mongo-shard-1:27017" },
      { _id: 1, host: "mongo-shard-2:27017" },
      { _id: 2, host: "mongo-shard-3:27017" }
    ]
  });
}
JS

echo "Waiting for shard RS..."
for _ in $(seq 1 60); do
  if mongosh "mongodb://mongo-shard-1:27017" --quiet --eval "try{rs.status().ok}catch(e){0}" 2>/dev/null | grep -q true; then
    break
  fi
  sleep 2
done

mongosh "mongodb://mongo-shard2-1:27017" --quiet <<'JS'
try {
  rs.status();
} catch (e) {
  rs.initiate({
    _id: "shardrepl2",
    members: [
      { _id: 0, host: "mongo-shard2-1:27017" },
      { _id: 1, host: "mongo-shard2-2:27017" },
      { _id: 2, host: "mongo-shard2-3:27017" }
    ]
  });
}
JS

echo "Waiting for shard2 RS..."
for _ in $(seq 1 60); do
  if mongosh "mongodb://mongo-shard2-1:27017" --quiet --eval "try{rs.status().ok}catch(e){0}" 2>/dev/null | grep -q true; then
    break
  fi
  sleep 2
done

echo "Waiting for mongos..."
for _ in $(seq 1 120); do
  if mongosh "mongodb://mongos:27017" --quiet --eval "db.runCommand({ping:1}).ok" 2>/dev/null | grep -q 1; then
    break
  fi
  sleep 2
done

mongosh "mongodb://mongos:27017" --quiet <<'JS'
try {
  sh.addShard("shardrepl/mongo-shard-1:27017,mongo-shard-2:27017,mongo-shard-3:27017");
} catch (e) {}

try {
  sh.addShard("shardrepl2/mongo-shard2-1:27017,mongo-shard2-2:27017,mongo-shard2-3:27017");
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
