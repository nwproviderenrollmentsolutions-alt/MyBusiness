#!/usr/bin/env bash
# Local Postgres helper for environments without Docker (e.g. dev containers where
# the cluster is installed but stopped). Creates the app role and both databases.
set -euo pipefail

DB_USER=${DB_USER:-mybusiness}
DB_PASSWORD=${DB_PASSWORD:-mybusiness_dev}
DB_NAME=${DB_NAME:-mybusiness}
TEST_DB_NAME=${TEST_DB_NAME:-mybusiness_test}

as_postgres() { su postgres -c "psql -tAc \"$1\""; }

case "${1:-start}" in
  start)
    service postgresql start >/dev/null 2>&1 || true
    for _ in $(seq 1 15); do
      pg_isready -q && break
      sleep 1
    done
    pg_isready || { echo "postgres failed to start" >&2; exit 1; }

    if [ "$(as_postgres "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'")" != "1" ]; then
      as_postgres "CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASSWORD}' CREATEDB" >/dev/null
    fi
    for db in "${DB_NAME}" "${TEST_DB_NAME}"; do
      if [ "$(as_postgres "SELECT 1 FROM pg_database WHERE datname='${db}'")" != "1" ]; then
        as_postgres "CREATE DATABASE ${db} OWNER ${DB_USER}" >/dev/null
      fi
    done
    echo "postgres ready: ${DB_NAME}, ${TEST_DB_NAME}"
    ;;
  stop)
    service postgresql stop
    ;;
  status)
    pg_lsclusters
    ;;
  *)
    echo "usage: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
