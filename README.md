# seko-ai

Self-service control plane for a shared local LLM backend (vLLM). Lets a small group of
trusted users manage their LLM API key and point their own harness/editor at the shared
model — all behind Authelia SSO.

> **Deprecation note (v0.3.0):** the hosted **Workspaces**, workspace **Backups**, and the
> **self-host Docker kit** are no longer exposed on the website (their routes return 404).
> There was no user demand — everyone uses the direct API route with their own harness. The
> code (routers, services, models, migrations, templates) is retained in the repo for now,
> just unwired from the app; re-mounting the routers in `app.py` brings them back.

Designed to integrate with the [`psx-homelab`](../psx-homelab) GitOps setup (Ansible +
Docker Compose, SOPS secrets, restic→NAS backups, Prometheus/Grafana, Caddy + Cloudflare
Tunnel).

## Architecture (summary)

- **Control plane** (this app): FastAPI + HTMX/Tailwind, SQLite, runs on `core-infra`.
- **Auth**: Authelia OIDC; access gated by the `llm_users` LLDAP group, admins via
  `homelab_admins`.
- **LLM keys**: per-user virtual keys via a **LiteLLM proxy** in front of vLLM.
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
