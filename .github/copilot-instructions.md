# Copilot Instructions for seko-ai

seko-ai is a **self-service control plane** that lets a small group (<3) of trusted
friends/family use a shared local LLM backend (vLLM on the homelab's `epyc` box) for
**agentic coding (pi) + chat**. Users manage everything from a web UI: SSO login, LLM API
keys, launching/terminating isolated **workspaces**, backups, and a self-host kit.

It is the productized, multi-tenant evolution of the manual `stacks/dev` + shared-vLLM-key
workflow in the sibling repo **`psx-homelab`** (the GitOps homelab). Read
`../psx-homelab/docs/seko-ai-runbook.md` for the deployment/operator guide, and this repo's
`backlog.md` for deferred work.

## Architecture

- **Control plane (this app):** FastAPI + HTMX/Tailwind (server-rendered, minimal JS),
  SQLite + Alembic, runs on **core-infra (10.37.20.10)** at `seko.pushprh.com`.
- **Auth:** Authelia OIDC (`auth.pushprh.com`). Access gated by the **`llm_users`** LLDAP
  group; **`homelab_admins`** ⇒ admin.
- **LLM keys:** a **LiteLLM proxy** (`stacks/litellm` on epyc, port 4000) fronts vLLM;
  seko-ai mints per-user *virtual keys* via LiteLLM's admin API. `llm.pushprh.com` is
  repointed to LiteLLM (was vLLM:8000) so the public endpoint enforces per-user keys.
- **Workspaces:** hardened per-user containers on **epyc** (image `ghcr.io/pushpreet/
  seko-workspace`, source in `workspace-image/`), orchestrated over the **Docker Engine API
  via SSH** (`DOCKER_HOST=ssh://seko@10.37.20.50`). Reached by **native SSH over
  Tailscale/LAN** (`ssh dev@10.37.20.50 -p <port>`). Per-user home is **gocryptfs-encrypted**.
- **Backups:** restic → NAS (nightly + on-demand + prompt-on-terminate).
- **Metrics:** Prometheus `/metrics` + in-app per-user usage (from LiteLLM).

Data flow: browser → Cloudflare Tunnel → Caddy → seko-ai; seko-ai → LiteLLM admin API
(keys) and → epyc Docker-over-SSH (workspaces, gocryptfs, restic).

## Repo layout

- `src/seko_ai/` — app. `app.py` (factory), `config.py` (pydantic-settings, `SEKO_` env),
  `auth.py` (OIDC), `deps.py` (DI), `models.py` (SQLAlchemy), `metrics.py`, `management.py`
  (nightly-backup / idle-reaper CLIs).
  - `routers/` — `auth, profile, keys, workspaces, backups, selfhost, usage, health`.
  - `services/` — `users, keys, litellm_client, crypto (envelope enc), workspaces
    (orchestration core + `ContainerBackend` protocol), docker_backend (Docker-over-SSH +
    gocryptfs/restic, I/O `# pragma: no cover`), backups, ssh_keys, kit, usage`.
- `migrations/` — Alembic (SQLite, **batch mode** — constraints MUST be named).
- `workspace-image/` — the portable workspace image: `Dockerfile`, `entrypoint.sh`,
  `sshd_config`, `pi-agent/` (pi provider config), `publish.sh` (GHCR).
- `tests/` — pytest (`unit/`, `integration/`, `fakes.py`, `conftest.py`).
- `Dockerfile` + `docker-entrypoint.sh` (runs `alembic upgrade head` then uvicorn).

## Dev workflow (uv)

```bash
uv sync
uv run ruff check . && uv run mypy src && uv run pytest        # all must pass
uv run pytest --cov          # keep coverage high (currently ~93%; target ≥90%)
./tasks.sh check             # lint + typecheck + cov

# Run a single test / file / dir (./tasks.sh test forwards args to pytest):
uv run pytest tests/unit/test_keys_service.py                  # one file
uv run pytest tests/unit/test_keys_service.py::test_mint_key   # one test
uv run pytest tests/unit -k "rotate"                           # by keyword
./tasks.sh test tests/unit/test_crypto.py                      # via task runner
```
Tests split into `tests/unit/` (services vs. `tests/fakes.py`) and `tests/integration/`
(routers via FastAPI `TestClient`). `pytest-asyncio` is in `auto` mode (no `@mark.asyncio`
needed); shared fixtures + a real ed25519 key live in `tests/conftest.py`.
Conventions: type-annotated, ruff-clean, mypy-strict. Business logic lives in `services/`
and is unit-tested against fakes (`tests/fakes.py`: `FakeLiteLLMClient`, `FakeBackend`);
Docker/SSH/gocryptfs/restic I/O is `# pragma: no cover` and validated on epyc by an operator.
Routers are thin. Errors that update HTMX panels return 4xx/422 **and still render** (see
HTMX gotcha below).

## Deployment (no CI — manual, via psx-homelab)

Images are **built on the hosts** (local Docker often unavailable; GHCR push needs the
user's creds): rsync repo → host → `docker build -t ghcr.io/pushpreet/seko-ai:0.1.0` →
`docker compose up -d --force-recreate`. seko-ai stack + secrets live in psx-homelab
(`stacks/seko-ai`, `secrets/seko-ai.env.sops`, `secrets/litellm.env.sops`). Deploy stacks
with `ansible-playbook site.yml --limit <core|llm> --tags stacks` (skips heavy roles).
The seko-ai container self-migrates on start.

## Critical gotchas (hard-won — do not regress)

1. **OIDC groups come from Authelia's USERINFO endpoint, not the ID token** — the callback
   must call `userinfo` after `authorize_access_token`, else `groups` is empty and valid
   users are denied.
2. **Workspace `dev` uid = 1001** to match the host `seko` user that mounts gocryptfs. Mount
   with `-allow_other` (NOT `-force_owner` — that's cosmetic; uid still can't write).
3. **gocryptfs under `/opt` needs AppArmor + fuse.conf** on epyc: `user_allow_other` +
   an allow-rule for `/opt/appdata/seko-ai/workspaces/**` (Ubuntu's fusermount3 profile only
   allows `$HOME/mnt/media/tmp`). Codified in psx-homelab `roles/llm_host`.
4. **Workspaces keep Docker's default caps** (NOT `cap_drop: ALL`) + `no-new-privileges`:
   sshd needs SETUID/SETGID/CHOWN/DAC_OVERRIDE (default_permissions is on).
5. **sshd host keys live in `/etc/ssh`** (root-owned), generated via `ssh-keygen -A` at start
   — a gocryptfs mount (served by unprivileged seko) can't hold root-owned files.
6. **pi must be configured to use LiteLLM**: `workspace-image/pi-agent/local-llm.ts`
   (provider extension reading `LLM_BASE_URL/LLM_API_KEY/LLM_MODEL`) + `settings.json`
   (`defaultProvider: local`), installed into `~/.pi/agent` by the entrypoint. Otherwise pi
   defaults to OpenAI. `fd` (10.4.2) is pre-installed so pi doesn't self-download it.
7. **`epyc.pushprh.com` resolves to core-infra** via the `*.pushprh.com` wildcard — use the
   **IP `10.37.20.50`** for workspace SSH (`SEKO_WORKSPACE_SSH_HOST`).
8. **HTMX**: `base.html` sets `htmx-config responseHandling` to swap 4xx/5xx so inline
   error/validation fragments render (default HTMX ignores error responses).
9. **Workspace-scoped LiteLLM keys** carry `ApiKey.workspace_id` and are hidden from the
   user's `/keys` list + revoked on terminate.
10. **SOPS encryption** of new secrets: place plaintext at the matching
    `secrets/<name>.env.sops` path then `sops -e -i` (creation_rules match the path;
    `-e input > output` fails). Don't clobber live `*.env.sops` via redirect.
11. **docker[ssh] extra (paramiko)** is required for `DOCKER_HOST=ssh://` even with
    `use_ssh_client=True`.
12. Alembic on SQLite uses **batch mode** — every constraint needs an explicit name.

## Live deployment state (as of last session)

Deployed and validated end-to-end on the real service: SSO login, SSH-keys UI, mint/rotate
key, launch/list/stop/terminate workspace, backup/restore, usage, self-host kit; real SSH
into a workspace; pi → LiteLLM → vLLM completion; encryption-at-rest confirmed. seko-ai
image is `0.1.0`. psx-homelab changes are on branch **`seko-ai-integration`** (not merged
to `main`).

**Operator-only / open items** (see `backlog.md` + runbook): public (no-Tailscale) workspace
access; the one-time Cloudflare CNAME `seko → <tunnel-UUID>` for internet access; GHCR image
publish for the self-host flow; existing workspaces must be recreated to pick up image changes.
