# seko workspace image — portable per-user agentic coding environment.
#
# This image is intentionally small and boring: one Ubuntu base, one non-root user, one
# harness (pi), and an embedded sshd. seko-ai runs the same image for hosted users on the
# LLM host and for self-hosted users pointed at the public OpenAI-compatible endpoint.
FROM ubuntu:24.04

# --- Versions (pin deliberately; bump when upgrading) ---------------------------------
ARG NODE_MAJOR=22
ARG PI_VERSION=0.80.3

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

# pi coding agent (npm @earendil-works/pi-coding-agent). Pin by default, override with
# --build-arg PI_VERSION=... when intentionally upgrading.
RUN npm install -g --ignore-scripts "@earendil-works/pi-coding-agent@${PI_VERSION}" \
    && npm cache clean --force

# --- Non-root user (uid/gid 1000 to match mounted home volumes) ------------------------
# The dev user's uid/gid MUST match the host `seko` user (1001) that mounts the per-user
# gocryptfs home on the LLM host, so the container owns its home and can write. ubuntu:24.04
# ships a default 'ubuntu' user at 1000 — remove it (we don't need 1000).
RUN userdel -r ubuntu 2>/dev/null || true; \
    groupadd -g 1001 dev; \
    useradd -m -u 1001 -g 1001 -s /bin/bash dev; \
    install -d -o dev -g dev -m 0755 /home/dev/workspace /home/dev/.config/seko /home/dev/.pi/agent; \
    install -d -m 0755 /run/sshd

COPY sshd_config /etc/ssh/sshd_config
COPY entrypoint.sh /usr/local/bin/seko-entrypoint
# pi config (provider extension + default settings), installed into ~/.pi/agent at runtime
# by the entrypoint (the mounted home would otherwise shadow anything baked into /home/dev).
COPY pi-agent/ /usr/local/share/seko-pi/
RUN chmod 0755 /usr/local/bin/seko-entrypoint \
    && chmod 0644 /etc/ssh/sshd_config

WORKDIR /home/dev
EXPOSE 22
VOLUME ["/home/dev"]

ENTRYPOINT ["/usr/local/bin/seko-entrypoint"]
CMD ["/usr/sbin/sshd", "-D", "-e"]
