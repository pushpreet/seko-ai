#!/usr/bin/env bash
# seko-ai task runner (no extra deps beyond uv). Usage: ./tasks.sh <task>
set -euo pipefail

UV="${UV:-uv}"

install() { $UV sync; }
lint()    { $UV run ruff check .; }
fmt()     { $UV run ruff format .; }
typecheck() { $UV run mypy src; }
test()    { $UV run pytest "$@"; }
cov()     { $UV run pytest --cov --cov-report=term-missing --cov-report=xml "$@"; }
run()     { $UV run seko-ai; }
migrate() { $UV run alembic upgrade head; }
revision() { $UV run alembic revision --autogenerate -m "${1:-change}"; }
check()   { lint && typecheck && cov; }

cmd="${1:-check}"; shift || true
"$cmd" "$@"
