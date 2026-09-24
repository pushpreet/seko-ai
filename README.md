# seko-ai

Self-service API-key control plane for a shared local LLM service. A small group of trusted
users signs in through an OIDC provider, manages named LiteLLM virtual keys, reviews usage,
reads client guidance, and sees current service status.

seko-ai is a product: it knows nothing about any particular deployment. Its operator
interface — every setting, port, volume, command, and endpoint — is documented in
[`docs/configuration.md`](docs/configuration.md); changes and upgrade notes are in
[`CHANGELOG.md`](CHANGELOG.md); [`examples/compose.yaml`](examples/compose.yaml) runs it
standalone.

## What it provides

- OIDC sign-in gated by a configured users group; a configured admins group grants admin
  views.
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

The container entrypoint applies `alembic upgrade head` before starting Uvicorn. The
availability probe runs in-process; no external scheduler is needed.

```bash
seko-ai maintenance start --message "planned work"
seko-ai maintenance start --owner my-automation --no-notify --json   # a lease
seko-ai maintenance status --json
seko-ai maintenance end
seko-ai backup /data/seko-ai.db.bak
```

See [`docs/configuration.md`](docs/configuration.md#operator-commands) for lease semantics
and the JSON output.

`SEKO_LLM_EMBEDDING_ENABLED` and `SEKO_LLM_IMAGE_GENERATION_ENABLED` (default `false`)
publish the corresponding setup cards, API examples and image MCP tools; chat and Qwen
image-input guidance is always shown. They control the published guidance, not LiteLLM routing: remove
the retired gateway routes separately. Blank model names are not disable switches.
Existing keys, conversations, uploads and vector indexes are not changed.

## Manual release

There is no CI release workflow. `./publish.sh` is the sole supported image release path:

1. Set the version in `pyproject.toml` and `src/seko_ai/__init__.py`, and add a
   `CHANGELOG.md` entry with upgrade notes for any configuration change.
2. Authenticate with `docker login ghcr.io`.
3. Commit the release so the worktree is clean.
4. Run `./publish.sh`.

The script rejects arguments, dirty worktrees, invalid versions, missing GHCR auth, and
existing version tags. It runs `./tasks.sh check`, builds only from the restrictive
`.dockerignore` context, applies OCI source/revision/version labels, pushes
`ghcr.io/pushpreet/seko-ai:<version>`, and prints the immutable digest.
