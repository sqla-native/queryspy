#!/usr/bin/env bash
# Run the gated integration suite against the compose databases.
set -euo pipefail

export QUERYSPY_POSTGRES_URL="postgresql+psycopg://queryspy:queryspy@127.0.0.1:55432/queryspy"
export QUERYSPY_POSTGRES_ASYNC_URL="postgresql+asyncpg://queryspy:queryspy@127.0.0.1:55432/queryspy"
export QUERYSPY_MYSQL_URL="mysql+pymysql://queryspy:queryspy@127.0.0.1:53306/queryspy"
export QUERYSPY_MYSQL_ASYNC_URL="mysql+aiomysql://queryspy:queryspy@127.0.0.1:53306/queryspy"

# `python` is not always on PATH outside an activated virtualenv or CI, and a
# script that only works in one of those is a script that gets abandoned.
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x .venv/bin/python ]; then
    PYTHON=.venv/bin/python
  else
    PYTHON="$(command -v python || command -v python3)"
  fi
fi

exec "$PYTHON" -m pytest tests/integration -v "$@"
