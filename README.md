# seko-ai

Self-service control plane for a shared local LLM backend (vLLM) and isolated agentic
coding workspaces. Lets a small group of trusted users manage their LLM API key, launch
hosted harness environments on the LLM host, or run the same portable harness on their own
hardware — all behind Authelia SSO.

Designed to integrate with the [`psx-homelab`](../psx-homelab) GitOps setup (Ansible +
Docker Compose, SOPS secrets, restic→NAS backups, Prometheus/Grafana, Caddy + Cloudflare
Tunnel).

## Architecture (summary)

- **Control plane** (this app): FastAPI + HTMX/Tailwind, SQLite, runs on `core-infra`.
- **Auth**: Authelia OIDC; access gated by the `llm_users` LLDAP group, admins via
  `homelab_admins`.
- **LLM keys**: per-user virtual keys via a **LiteLLM proxy** in front of vLLM.
- **Workspaces**: hardened per-user containers on `epyc`, orchestrated over the Docker API
  (SSH-tunneled), reached by native SSH over Tailscale.
- **Data**: per-user home volumes are envelope-encrypted (admin-held keys) and backed up
  with restic (nightly + on-demand + on-terminate).

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
