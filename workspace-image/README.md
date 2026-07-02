# seko workspace image

Portable per-user workspace for seko-ai. The same image runs hosted on `epyc` (users SSH in
over Tailscale) or self-hosted on a user's machine pointed at `https://llm.pushprh.com/v1`.

## Contents

- Ubuntu 24.04
- non-root `dev` user (`uid=1000`, `gid=1000`, `HOME=/home/dev`)
- git, Node 22, python3, tmux, fzf, ripgrep, curl, ca-certificates, OpenSSH server
- pi coding agent: npm `@earendil-works/pi-coding-agent` pinned by `PI_VERSION` (default `0.80.3`); pi is preconfigured to use the LiteLLM endpoint via a bundled provider extension

Build args:

```bash
docker build \
  --build-arg NODE_MAJOR=22 \
  --build-arg PI_VERSION=0.80.2 \
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
durable mounted volume for dotfiles, pi state, SSH auth, and `~/workspace`.

Host keys are generated on first run under `/home/dev/.ssh/host_keys/` and referenced by
`sshd_config`. Because that directory is on the mounted home volume, SSH fingerprints remain
stable across container recreation. If the home volume is deleted, new host keys are generated
and clients will see a changed fingerprint.

## Hosted vs self-hosted

Hosted seko-ai containers run on the LLM host and expose SSH only through the orchestrator's
Tailscale-only ingress. Self-hosted users run the same image locally and set
`LLM_BASE_URL=https://llm.pushprh.com/v1` plus their issued `LLM_API_KEY`. The image has no
Docker socket requirement, no privileged mode requirement, and no dependency on a specific
Docker network. For local host access, add runtime host-gateway wiring outside the image, e.g.:

```bash
docker run --add-host host.docker.internal:host-gateway ...
```

## pi configuration

This image does not bake a `local-harness` repository or custom pi extension. It creates
`~/.pi/agent` and relies on the exported `LLM_*` and `OPENAI_*` variables, which pi and other
OpenAI-compatible tooling can consume. User-specific pi config and sessions persist under
`/home/dev`.

## Publish

```bash
./publish.sh 0.1.0
```

The script builds and pushes `ghcr.io/pushpreet/seko-workspace:<tag>` and `:latest`. Run
`docker login ghcr.io` first, then make the GHCR package public in the GitHub package settings.
