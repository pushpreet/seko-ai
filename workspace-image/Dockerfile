# seko workspace image — portable per-user agentic coding environment.
#
# This image is intentionally small and boring: one Ubuntu base, one non-root user, two
# interchangeable harnesses (pi + oh-my-pi/omp), and an embedded sshd. seko-ai runs the same
# image for hosted users on the LLM host and for self-hosted users pointed at the public
# OpenAI-compatible endpoint; users pick which harness to drive per workspace.
FROM ubuntu:24.04

# --- Versions (pin deliberately; bump when upgrading) ---------------------------------
ARG NODE_MAJOR=22
ARG PI_VERSION=0.80.3
# oh-my-pi (omp) — second harness. Installed as the self-contained prebuilt binary from
# GitHub releases (bundles its own Bun runtime + native addon; no Node/Bun needed at run).
ARG OMP_VERSION=v16.3.2
# fd (fast file finder) — pi uses it and otherwise downloads its own copy on first run.
# Pin to the version pi expects so it's found on PATH (like ripgrep) and never downloaded.
ARG FD_VERSION=10.4.2

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/home/dev

# Fail pipelines (e.g. `curl ... | bash`) on the first non-zero status.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# --- Base harness layer ---------------------------------------------------------------
# Keep this aligned with the homelab dev image's portable layer: git, Node, python3,
# tmux, fzf, ripgrep, curl, ca-certificates, and OpenSSH. gnupg is only needed for the
# NodeSource repository bootstrap.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git tmux fzf ripgrep \
        python3 python3-pip python3-venv \
        openssh-server openssh-client less vim \
    && rm -f /etc/ssh/ssh_host_*_key /etc/ssh/ssh_host_*_key.pub \
    && rm -rf /var/lib/apt/lists/*

# Node.js (NodeSource) — runtime for the npm-based pi coding harness.
RUN curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# pi coding agent (npm @earendil-works/pi-coding-agent). Pinned baseline via PI_VERSION;
# override with --build-arg PI_VERSION=... . Installed into a dev-owned npm prefix (/opt/pi)
# and symlinked onto the stock PATH so `pi update self` can self-update in-container: login
# shells prepend /opt/pi/bin and set NPM_CONFIG_PREFIX to the dev-owned tree (see
# /etc/profile.d/seko-harness.sh). Like omp, self-updates live on the container filesystem
# (outside the mounted home) and reset to this pinned baseline on workspace recreate.
RUN npm install -g --ignore-scripts --prefix /opt/pi "@earendil-works/pi-coding-agent@${PI_VERSION}" \
    && npm cache clean --force \
    && chown -R 1001:1001 /opt/pi \
    && ln -sf /opt/pi/bin/pi /usr/local/bin/pi

# oh-my-pi (omp) — second harness, pinned prebuilt linux-x64 binary (self-contained: bundles
# Bun + the pi_natives NAPI addon, so no Node/Bun runtime is needed). Override the baseline
# with --build-arg OMP_VERSION=vX.Y.Z. Installed under /opt/omp/bin owned by the dev user
# (uid 1001) and symlinked onto the stock PATH, so `omp update` can self-update in-container
# the same way pi does (login shells prepend /opt/omp/bin — see /etc/profile.d/seko-harness.sh).
RUN install -d -m 0755 -o 1001 -g 1001 /opt/omp/bin \
    && curl -fsSL "https://github.com/can1357/oh-my-pi/releases/download/${OMP_VERSION}/omp-linux-x64" \
      -o /opt/omp/bin/omp \
    && chown 1001:1001 /opt/omp/bin/omp \
    && chmod 0755 /opt/omp/bin/omp \
    && ln -sf /opt/omp/bin/omp /usr/local/bin/omp \
    && /opt/omp/bin/omp --version

# Put the dev-writable harness dirs first on the login PATH and point npm's global prefix at
# the dev-owned tree, so `pi update self` and `omp update` write to dirs the dev user owns.
RUN printf '%s\n' \
      '# seko: prefer the dev-writable harness installs so self-update works in-container.' \
      'export PATH="/opt/pi/bin:/opt/omp/bin:$PATH"' \
      'export NPM_CONFIG_PREFIX="/opt/pi"' \
      > /etc/profile.d/seko-harness.sh \
    && chmod 0644 /etc/profile.d/seko-harness.sh

# fd on PATH (matches the version pi would otherwise self-download to ~/.pi/agent/bin/fd).
RUN curl -fsSL "https://github.com/sharkdp/fd/releases/download/v${FD_VERSION}/fd-v${FD_VERSION}-x86_64-unknown-linux-gnu.tar.gz" \
      | tar -xz -C /tmp \
    && install -m 0755 "/tmp/fd-v${FD_VERSION}-x86_64-unknown-linux-gnu/fd" /usr/local/bin/fd \
    && rm -rf "/tmp/fd-v${FD_VERSION}-x86_64-unknown-linux-gnu"

# --- Non-root user (uid/gid 1000 to match mounted home volumes) ------------------------
# The dev user's uid/gid MUST match the host `seko` user (1001) that mounts the per-user
# gocryptfs home on the LLM host, so the container owns its home and can write. ubuntu:24.04
# ships a default 'ubuntu' user at 1000 — remove it (we don't need 1000).
RUN userdel -r ubuntu 2>/dev/null || true; \
    groupadd -g 1001 dev; \
    useradd -m -u 1001 -g 1001 -s /bin/bash dev; \
    install -d -o dev -g dev -m 0755 /home/dev/workspace /home/dev/.config/seko \
        /home/dev/.pi/agent /home/dev/.omp/agent; \
    install -d -m 0755 /run/sshd; \
    # Earlier build steps run pi/omp/npm as root with HOME=/home/dev (e.g. `omp --version`),
    # leaving ~/.omp, ~/.pi, ~/.npm and ~/.config owned root:root. The dev user must own its
    # whole home so `omp` can create ~/.omp/natives (else its bundled native addon can't load
    # and it won't start) and so `pi update self`/`omp update` and npm cache writes work.
    chown -R dev:dev /home/dev

COPY sshd_config /etc/ssh/sshd_config
COPY entrypoint.sh /usr/local/bin/seko-entrypoint
# Harness config, installed into the mounted home at runtime by the entrypoint (the mounted
# home would otherwise shadow anything baked into /home/dev):
#   pi  -> ~/.pi/agent   (provider extension + settings.json)
#   omp -> ~/.omp/agent  (provider extension + config.yml)
COPY pi-agent/ /usr/local/share/seko-pi/
COPY omp-agent/ /usr/local/share/seko-omp/
RUN chmod 0755 /usr/local/bin/seko-entrypoint \
    && chmod 0644 /etc/ssh/sshd_config

WORKDIR /home/dev
EXPOSE 22
VOLUME ["/home/dev"]

ENTRYPOINT ["/usr/local/bin/seko-entrypoint"]
CMD ["/usr/sbin/sshd", "-D", "-e"]
