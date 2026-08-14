# seko-ai

Self-service control plane for a shared local LLM backend (vLLM). Lets a small group of
trusted users manage their LLM API key and point their own harness/editor at the shared
model — all behind Authelia SSO.

> **Deprecation note (v0.3.0):** the hosted **Workspaces**, workspace **Backups**, and the
> **self-host Docker kit** are no longer exposed on the website (their routes return 404).
> There was no user demand — everyone uses the direct API route with their own harness. The
> code (routers, services, models, migrations, templates) is retained in the repo for now,
> just unwired from the app; re-mounting the routers in `app.py` brings them back.
> Before that retained code is removed, operators can inventory and retire its production
> resources with the management command documented below.

Designed to integrate with the [`psx-homelab`](../psx-homelab) GitOps setup (Ansible +
Docker Compose, SOPS secrets, restic→NAS backups, Prometheus/Grafana, Caddy + Cloudflare
Tunnel).

## Architecture (summary)

- **Control plane** (this app): FastAPI + HTMX/Tailwind, SQLite, runs on `core-infra`.
- **Auth**: Authelia OIDC; access gated by the `llm_users` LLDAP group, admins via
  `homelab_admins`.
- **LLM keys**: user-named, editable virtual keys via a **LiteLLM proxy** in front of vLLM;
  names and usage history stay together when a token is rotated.
- **Usage**: 30-day user totals with collapsible per-key and per-model token/request
  breakdowns; admins can also inspect service and unattributed keys.
- **Deprecated (code retained, hidden from the UI)**: hosted **Workspaces** (hardened
  per-user containers on `epyc` over Docker-over-SSH), restic **Backups**, and the
  **self-host kit**. See the deprecation note above.

See the implementation plan for the full design.

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                 # create .venv and install deps (incl. dev group)
cp .env.example .env     # fill in secrets for local dev
uv run seko-ai           # run the dev server on :8080

uv run ruff check .      # lint
uv run mypy src          # type-check
uv run pytest            # tests
uv run pytest --cov      # tests with coverage
```

Or use the task runner:

```bash
./tasks.sh install | lint | typecheck | test | cov | run
```

## Manual release

There is no CI release workflow. `./publish.sh` is the only supported control-plane image
release path. First run `docker login ghcr.io`; the script then reads the version from
`pyproject.toml`, requires a clean worktree and unused version tag, runs `./tasks.sh check`,
builds with OCI source/revision/version labels, and pushes only
`ghcr.io/pushpreet/seko-ai:<version>`. It prints the resulting immutable `tag@digest`.

## Intermediate workspace retirement

The deprecated workspace implementation is intentionally retained in this release so it can
clean existing production resources before final removal. The command defaults to a
secret-free dry run:

```bash
python -m seko_ai.management retire-workspaces
```

After reviewing the inventory, run the destructive cleanup only with the exact confirmation
printed by the dry run. Failures are surfaced and leave database metadata available for a
safe rerun. Normal user API keys and service-status history are preserved.
