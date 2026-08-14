# seko-ai

Self-service API-key control plane for a shared local LLM service. A small group of trusted
users signs in through Authelia, manages named LiteLLM virtual keys, reviews usage, reads
client guidance, and sees current service status.

## What it provides

- Authelia OIDC sign-in, gated by `llm_users`; `homelab_admins` grants admin views.
- Named API keys whose display identity and usage history survive token rotation.
- One-time key reveal plus rotate, rename, and revoke actions.
- 30-day per-user, per-key, and per-model usage; admins also see service and unattributed
  keys.
- OpenAI-compatible chat, embeddings, image, and MCP setup guidance.
- Persisted availability status, recent incidents, maintenance windows, Resend
  notifications, and Prometheus metrics.

The application is FastAPI with server-rendered Jinja/HTMX pages, SQLAlchemy/Alembic, and
SQLite. LiteLLM is the only managed backend.

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env
uv run seko-ai

./tasks.sh lint
./tasks.sh typecheck
./tasks.sh test
./tasks.sh cov
./tasks.sh check
```

`./tasks.sh check` runs Ruff, strict mypy, and the coverage-enabled pytest suite. Alembic
migrations use SQLite batch mode; run them locally with `./tasks.sh migrate`.

## Operations

The container entrypoint applies `alembic upgrade head` before starting Uvicorn.

```bash
python -m seko_ai.management check-status
python -m seko_ai.management maintenance start --message "planned work"
python -m seko_ai.management maintenance status
python -m seko_ai.management maintenance end
```

`check-status` is intended for the host timer. Maintenance suppresses transition alerts and
can send start/end notices according to configuration.

## Manual release

There is no CI release workflow. `./publish.sh` is the sole supported image release path:

1. Set the version in `pyproject.toml`.
2. Authenticate with `docker login ghcr.io`.
3. Commit the release so the worktree is clean.
4. Run `./publish.sh`.

The script rejects arguments, dirty worktrees, invalid versions, missing GHCR auth, and
existing version tags. It runs `./tasks.sh check`, builds only from the restrictive
`.dockerignore` context, applies OCI source/revision/version labels, pushes
`ghcr.io/pushpreet/seko-ai:<version>`, and prints the immutable digest.
