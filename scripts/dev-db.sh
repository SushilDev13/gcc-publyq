#!/usr/bin/env bash
# Isolated local Postgres for GCC Publyq: own data dir (.local/pg), own port
# (5433), trust auth on localhost only. Never touches the Publyq DB on 5432.
set -euo pipefail
export LC_ALL=C LANG=C
cd "$(dirname "$0")/.."
PGBIN="${PGBIN:-/opt/homebrew/opt/postgresql@18/bin}"
DATA=.local/pg
PORT="${GCC_PG_PORT:-5444}"

case "${1:-start}" in
  start)
    if [ ! -d "$DATA" ]; then
      mkdir -p .local
      "$PGBIN/initdb" -D "$DATA" -U gcc --auth=trust -E UTF8 --locale=C >/dev/null
    fi
    "$PGBIN/pg_ctl" -D "$DATA" -o "-p $PORT -k /tmp -c listen_addresses=localhost" -l .local/pg.log -w status >/dev/null 2>&1 \
      || "$PGBIN/pg_ctl" -D "$DATA" -o "-p $PORT -k /tmp -c listen_addresses=localhost" -l .local/pg.log -w start
    "$PGBIN/psql" -h localhost -p "$PORT" -U gcc -d postgres -tAc "select 1 from pg_database where datname='gcc'" | grep -q 1 \
      || "$PGBIN/createdb" -h localhost -p "$PORT" -U gcc gcc
    echo "DATABASE_URL=postgresql://gcc@localhost:$PORT/gcc"
    ;;
  stop) "$PGBIN/pg_ctl" -D "$DATA" -w stop ;;
  *) echo "usage: $0 [start|stop]"; exit 1 ;;
esac
