# seko workspace image

Portable per-user workspace for seko-ai. The same image runs hosted on `epyc` (users SSH in
over Tailscale) or self-hosted on a user's machine pointed at `https://llm.pushprh.com/v1`.

## Contents

- Ubuntu 24.04
- non-root `dev` user (`uid=1001`, `gid=1001`, `HOME=/home/dev`)
- git, Node 22, python3, tmux, fzf, ripgrep, curl, ca-certificates, OpenSSH server
- two interchangeable coding harnesses, both preconfigured for the LiteLLM endpoint via a
  bundled provider extension so users can compare them:
  - **pi**: npm `@earendil-works/pi-coding-agent`, pinned by `PI_VERSION` (default `0.80.3`);
    launch with `pi`. Installed into a `dev`-owned npm prefix (`/opt/pi`) and symlinked onto
    `PATH`, so `pi update self` self-updates in-container from an interactive shell (updates
    reset to the pinned `PI_VERSION` baseline when the workspace is recreated).
  - **oh-my-pi (omp)**: prebuilt binary pinned by `OMP_VERSION` (default `v16.3.2`), a
    self-contained `omp-linux-x64` release (bundles its own Bun runtime — no Node/Bun
    dependency at runtime); launch with `omp`. Installed under `/opt/omp/bin` (owned by the
    `dev` user) and symlinked onto `PATH`, so `omp update` self-updates in-container from an
    interactive shell. Self-updates live on the container filesystem and reset to the pinned
    `OMP_VERSION` baseline when the workspace is recreated.

Build args:

```bash
docker build \
  --build-arg NODE_MAJOR=22 \
  --build-arg PI_VERSION=0.80.2 \
  --build-arg OMP_VERSION=v16.3.2 \
  -t seko-workspace:test .
```

## Runtime contract

seko-ai passes:

- `SEKO_AUTHORIZED_KEYS`: newline-separated public keys for the `dev` account
- `LLM_BASE_URL`: OpenAI-compatible endpoint, e.g. `http://host.docker.internal:8000/v1` or `https://llm.pushprh.com/v1`
- `LLM_API_KEY`: issued key for that endpoint
- `LLM_MODEL`: default model name

The entrypoint also reads `/run/secrets/authorized_keys` if mounted. It rewrites
`/home/dev/.ssh/authorized_keys` with mode `0600`; `/home/dev/.ssh` is `0700`. If no keys are
provided, it logs a warning and SSH login is unavailable.

LLM values are written to `/home/dev/.config/seko/llm.env` and mirrored as
`OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL`. The entrypoint sources that file from
`~/.bashrc` and `~/.profile`, writes `/etc/profile.d/seko-llm.sh`, and updates
`/etc/environment` so both interactive SSH sessions and `ssh host command` see the variables.

## SSH and persistence

`sshd` is the foreground process:

```bash
ssh dev@<host> -p <port>
```

Authentication is key-only; password auth and root login are disabled. `/home/dev` is the
durable mounted volume for dotfiles, harness state, SSH auth, and `~/workspace`.

Host keys live in `/etc/ssh` (root-owned, on the container filesystem — a gocryptfs mount
served by the unprivileged host user can't hold root-owned files). They persist across
stop/start; a terminate+recreate regenerates them, so a restored workspace is a new container
with a changed fingerprint (expected).

## Hosted vs self-hosted

Hosted seko-ai containers run on the LLM host and expose SSH only through the orchestrator's
Tailscale-only ingress. Self-hosted users run the same image locally and set
`LLM_BASE_URL=https://llm.pushprh.com/v1` plus their issued `LLM_API_KEY`. The image has no
Docker socket requirement, no privileged mode requirement, and no dependency on a specific
Docker network. For local host access, add runtime host-gateway wiring outside the image, e.g.:

```bash
docker run --add-host host.docker.internal:host-gateway ...
```

## Harness configuration

The entrypoint installs each harness's config into the mounted home at runtime (baked config
lives outside the mount so the volume can't shadow it):

- **pi** → `~/.pi/agent`: a managed `extensions/local-llm.ts` provider extension (always
  refreshed) plus a seeded `settings.json` (`defaultProvider: local`, `defaultModel` tracks
  `LLM_MODEL`).
- **omp** → `~/.omp/agent`: a managed `extensions/local-llm.ts` provider extension plus a
  seeded `config.yml` (`modelRoles.default: local/<LLM_MODEL>`, setup wizard skipped).

Both extensions register a `local` provider pointed at `LLM_BASE_URL` with `LLM_API_KEY`, so
`pi` and `omp` reach the same endpoint identically. User-specific harness config and sessions
persist under `/home/dev`. Run `pi` or `omp` inside the workspace to drive either one.

## Publish

```bash
./publish.sh 0.1.0
```

The script builds and pushes `ghcr.io/pushpreet/seko-workspace:<tag>` and `:latest`. Run
`docker login ghcr.io` first, then make the GHCR package public in the GitHub package settings.
