# Copilot Instructions for seko-ai

seko-ai is an API-key control plane for a shared LiteLLM-backed local LLM service. Keep the
application focused on OIDC users, named virtual keys, usage reporting, docs, availability
status, notifications, and maintenance.

## Architecture

- FastAPI + Jinja/HTMX, with minimal browser JavaScript.
- SQLite + SQLAlchemy + Alembic; production migrations run at container startup.
- OIDC (tested with Authelia). Groups must be read from the userinfo endpoint:
  `SEKO_OIDC_USERS_GROUP` grants access and `SEKO_OIDC_ADMINS_GROUP` grants admin views.
- In-process status scheduler (`scheduler.py`) started from the app lifespan; recurring work
  never depends on an external timer.
- LiteLLM admin API for key creation/revocation and usage activity.
- Prometheus `/metrics`, persisted service status/incidents, and Resend notifications.

## Layout

- `src/seko_ai/app.py` — application factory and router registration.
- `src/seko_ai/config.py` — `SEKO_` environment settings.
- `src/seko_ai/auth.py` and `deps.py` — OIDC/session and request dependencies.
- `src/seko_ai/models.py` — users, logical key identities, physical API keys, and status
  tables.
- `src/seko_ai/routers/` — auth, keys, usage, docs, status, and health/metrics.
- `src/seko_ai/services/` — users, keys, LiteLLM client, usage, status, and notifications.
- `migrations/` — Alembic; SQLite alterations use batch mode and named constraints.
- `tests/` — unit and FastAPI integration tests.

## Workflow

```bash
uv sync
./tasks.sh lint
./tasks.sh typecheck
./tasks.sh test
./tasks.sh cov
./tasks.sh check
```

Python is 3.12+, fully type-annotated, Ruff-clean, and strict-mypy clean. Put business logic
in services and keep routers thin. HTMX errors should render useful fragments; `base.html`
configures swaps for 4xx/5xx responses.

Named API keys use `ApiKeyIdentity` for the stable user-facing name and `ApiKey` for each
physical token. Rotation must create the replacement before revoking the old token, clean up
failed replacements, and retain historical rows for usage attribution. Do not send a model
allowlist when creating LiteLLM keys: absent/empty means all proxy models, while `["*"]`
breaks `GET /v1/models`.

## Release model

There are no GitHub Actions release workflows. `./publish.sh` is the only supported image
publisher. It derives the version from `pyproject.toml`, requires a clean committed
worktree, checks that the GHCR tag is unused, runs `./tasks.sh check`, builds with the
restrictive `.dockerignore`, pushes the version tag, and prints its digest. Do not weaken
those checks or add a second publishing path.

## Product boundary

seko-ai is a product; any deployment (including the author's homelab) is just one consumer.
`docs/configuration.md` is the public contract: every setting, volume, port, command, JSON
output, and endpoint a deployment may rely on. Keep it, `CHANGELOG.md` (with upgrade notes;
breaking config change = minor bump below 1.0, major after), `.env.example`, and
`examples/compose.yaml` in sync with any change. Never add deployment hostnames, IP
addresses, network names, host paths, or group names as defaults, in code, templates, or
tests (use `example.test`). Never ship deployment secrets.

## Critical invariants

1. Fetch OIDC userinfo after token exchange so group authorization is accurate.
2. Never persist or redisplay plaintext virtual keys.
3. Keep key operations scoped to the signed-in database user.
4. Keep normal keys available to all current and future proxy models.
5. Preserve users, named key identities, normal key history, service state, and status
   events across migrations.
6. Use explicitly named constraints in SQLite batch migrations.
7. Keep image and embeddings docs aligned with live settings without exposing backend
   credentials or private internal services.
