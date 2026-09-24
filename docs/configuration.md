# Configuration contract

This document is seko-ai's public operator interface. A deployment may rely on everything
listed here and nothing else — not on module imports and not on the database layout.
Changes are recorded in [`CHANGELOG.md`](../CHANGELOG.md); a breaking change bumps the
major version (the minor version while below 1.0).

## Container

| Item | Value |
|------|-------|
| Image | `ghcr.io/pushpreet/seko-ai:<version>` (deploy pinned by digest) |
| User | uid/gid `1000` (`seko`) |
| Port | `8080/tcp`, plain HTTP; terminate TLS in a reverse proxy |
| Volume | one writable directory for the SQLite database, conventionally `/data` with `SEKO_DATABASE_URL=sqlite:////data/seko-ai.db`; owned by uid 1000, on local disk (not NFS) |
| Startup | the entrypoint runs `alembic upgrade head`, then Uvicorn; migrations are automatic |
| Health | `GET /healthz` → `200` when serving (the image also has a `HEALTHCHECK`) |
| Metrics | `GET /metrics`, Prometheus exposition, unauthenticated — keep it off the public edge |

Recurring work runs inside the application: the availability probe runs every
`SEKO_STATUS_PROBE_INTERVAL` seconds; no external timer is needed. Several processes sharing
one database probe at most once per half-interval.

## Environment variables

All variables use the `SEKO_` prefix. **Required** variables have no default; the
application refuses to start without them.

### Core

| Variable | Default | Meaning |
|----------|---------|---------|
| `SEKO_BASE_URL` | **required** | public origin, e.g. `https://seko.example.com`; the OIDC redirect URI is this plus `SEKO_OIDC_REDIRECT_PATH`; `https` marks the session cookie `Secure` |
| `SEKO_SESSION_SECRET` | **required** | cookie-signing key (`openssl rand -hex 32`) |
| `SEKO_DATABASE_URL` | `sqlite:///./seko-ai.db` | SQLAlchemy URL; only file-backed SQLite is supported |
| `SEKO_DEBUG` | `false` | console logs and autoreload (development only) |

### OIDC

Any OIDC provider that returns a `groups` claim (in the ID token or userinfo) works; the
client requests scopes `openid profile email groups`. Tested with Authelia.

| Variable | Default | Meaning |
|----------|---------|---------|
| `SEKO_OIDC_ISSUER` | **required** | issuer URL; discovery at `/.well-known/openid-configuration` |
| `SEKO_OIDC_CLIENT_ID` | `seko-ai` | client ID |
| `SEKO_OIDC_CLIENT_SECRET` | empty | client secret |
| `SEKO_OIDC_REDIRECT_PATH` | `/auth/callback` | callback path to register with the provider |
| `SEKO_OIDC_USERS_GROUP` | **required** | members may sign in |
| `SEKO_OIDC_ADMINS_GROUP` | **required** | members may sign in and see admin views |

### LiteLLM gateway

| Variable | Default | Meaning |
|----------|---------|---------|
| `SEKO_LITELLM_BASE_URL` | **required** | gateway URL reachable from the container, e.g. `http://litellm:4000` |
| `SEKO_LITELLM_MASTER_KEY` | empty | master key: mints/inspects/revokes virtual keys and probes `/health` |
| `SEKO_SERVICE_USAGE_ALIASES` | empty | comma-separated key-alias prefixes shown as "Services / Agents" in admin usage |

### User guidance (`/docs`, `/keys`)

| Variable | Default | Meaning |
|----------|---------|---------|
| `SEKO_LLM_PUBLIC_URL` | **required** | OpenAI-compatible base URL handed to users, e.g. `https://llm.example.com/v1` |
| `SEKO_LLM_MODEL` | **required** | chat model name used in examples |
| `SEKO_LLM_MCP_URL` | derived | streamable-HTTP MCP endpoint; default replaces the public URL's `/v1` with `/mcp/` |
| `SEKO_CHAT_URL` | empty | root of a hosted chat UI (Open WebUI); empty = no link in the guide |
| `SEKO_LLM_EMBEDDING_ENABLED` | `false` | show embeddings guidance |
| `SEKO_LLM_EMBEDDING_MODEL` | empty | required when embeddings are enabled |
| `SEKO_LLM_EMBEDDING_DIMENSION` | `0` | vector size; required (non-zero) when enabled |
| `SEKO_LLM_IMAGE_GENERATION_ENABLED` | `false` | show image-generation guidance and image MCP tools |
| `SEKO_LLM_IMAGE_MODEL` | empty | required when image generation is enabled |
| `SEKO_LLM_IMAGE_QUALITY_MODEL` | empty | required when image generation is enabled |

These switches control published guidance only; gateway routing is configured in LiteLLM.

### Availability status and notifications

| Variable | Default | Meaning |
|----------|---------|---------|
| `SEKO_STATUS_SCHEDULER_ENABLED` | `true` | run the in-process probe loop |
| `SEKO_STATUS_PROBE_INTERVAL` | `60` | seconds between probes (positive) |
| `SEKO_STATUS_PROBE_MODE` | `litellm_health` | `litellm_health`: up iff LiteLLM `/health` reports ≥1 healthy endpoint; `http`: up iff `SEKO_STATUS_PROBE_URL` returns 200 |
| `SEKO_STATUS_PROBE_URL` | empty | URL for `http` mode (default `<litellm>/health/liveliness`) |
| `SEKO_STATUS_PROBE_TIMEOUT` | `10` | probe timeout, seconds |
| `SEKO_STATUS_FAIL_THRESHOLD` | `3` | consecutive failures before DOWN; one success returns UP |
| `SEKO_RESEND_API_KEY` | empty | Resend API key; empty disables all email |
| `SEKO_ALERT_EMAIL_FROM` | empty | sender on a Resend-verified domain; required when a key is set |
| `SEKO_STATUS_ALERT_ADMINS_ONLY` | `false` | email only admins about transitions and maintenance |
| `SEKO_STATUS_NOTIFY_ON_MAINTENANCE` | `true` | send maintenance start/end announcements |
| `SEKO_MAINTENANCE_MAX_HOURS` | `12` | auto-end a forgotten window (`0` = never) |

## Operator commands

Run inside the container (`docker exec seko-ai seko-ai <command>`). Logs go to stderr;
results go to stdout. `python -m seko_ai.management <command>` is an equivalent alias.

| Command | Result |
|---------|--------|
| `seko-ai [serve]` | run the web application |
| `seko-ai check-status` | probe once and record the result (the scheduler does this automatically) |
| `seko-ai maintenance start [--message TEXT] [--owner TOKEN] [--no-notify] [--json]` | open or join a window |
| `seko-ai maintenance end [--owner TOKEN] [--no-notify] [--json]` | close a window, or release a lease |
| `seko-ai maintenance status [--json]` | report the window |
| `seko-ai backup DEST` | write a consistent online SQLite copy to `DEST` (atomic rename); prints the path |

**Manual windows vs leases.** Without `--owner`, `start` opens the window (taking over an
already-open one as manual) and `end` closes it unconditionally. With `--owner`
(`[a-z0-9][a-z0-9-]{0,63}`) the command takes a *lease*: `start` joins or opens a leased
window but never takes over a manual one (it reports `owned: false`), and `end` releases only
that owner's lease; the window closes when the last owner releases it. Automation should use
leases so it can never end a window an operator opened.

`--json` prints one object:

```json
{"active": true, "message": "GPU swap", "owners": ["llm-op1"],
 "started_at": "2026-09-24T07:00:00+00:00", "status": "up", "owned": true}
```

`owned` is present for `--owner` calls; `window_ended` additionally for `end --owner`.
`status` is `up`, `down`, or `unknown`. Exit status is `0` on success, `2` on usage errors.

## HTTP interface

| Route | Access | Purpose |
|-------|--------|---------|
| `GET /healthz`, `GET /metrics` | public | health and metrics |
| `GET /auth/login`, `/auth/callback`, `/auth/logout` | public | OIDC flow |
| `GET /`, `/keys`, `/usage`, `/docs`, `/status` | signed-in users | browser pages |
| `POST /keys…`, `POST /status/maintenance/{start,end}` | users / admins | browser forms, not a machine API |

Automation should use the operator commands.

## Standalone example

[`examples/compose.yaml`](../examples/compose.yaml) with
[`examples/.env.example`](../examples/.env.example) runs seko-ai with only an OIDC provider
and a LiteLLM gateway.
