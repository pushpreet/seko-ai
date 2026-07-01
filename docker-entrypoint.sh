#!/usr/bin/env bash
# Container entrypoint: apply DB migrations, then run the given command (uvicorn by default).
set -euo pipefail

echo "[seko-ai] applying database migrations..."
alembic upgrade head

echo "[seko-ai] starting: $*"
exec "$@"
