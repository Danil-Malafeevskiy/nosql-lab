#!/bin/bash
set -euo pipefail

CASSANDRA_INIT_HOST="${CASSANDRA_HOSTS%%,*}"

for _ in $(seq 1 180); do
  if cqlsh "${CASSANDRA_INIT_HOST}" "${CASSANDRA_PORT}" -e "DESCRIBE KEYSPACES" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

cqlsh "${CASSANDRA_INIT_HOST}" "${CASSANDRA_PORT}" < <(
  sed "s/{{CASSANDRA_KEYSPACE}}/${CASSANDRA_KEYSPACE}/g" /init.cql
)
