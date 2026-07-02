# seko-ai backlog

Deferred features and improvements, most recent first.

> **Shipped:** In-app **Docs** page (`/docs`) covering the three ways to use the LLM (direct
> API, local workspaces, remote workspaces). Remote-workspace Tailscale onboarding uses
> **tailnet invite + ACL grant** scoping guests to the workspace SSH ports (`22000-22099`) —
> admin procedure in `../psx-homelab/docs/seko-ai-runbook.md` §5a. The item below (public,
> no-Tailscale access) remains the deferred, install-nothing alternative.

## Public (no-Tailscale) workspace access

**Status:** deferred · **Requested:** 2026-07-01

Today, hosted-workspace SSH is reachable only over Tailscale/LAN at
`ssh dev@10.37.20.50 -p <port>`. We want a way for friends/family to reach a workspace
**publicly without Tailscale**, ideally reusing the `seko.pushprh.com` domain.

**Why it's not a trivial "add a Cloudflare URL":** workspace access is raw **SSH (TCP)**,
while Cloudflare Tunnel is built for **HTTP**. Each workspace also runs its own `sshd` on a
**distinct port** (22000, 22001, …), so "many dynamic workspaces on one subdomain" doesn't
map cleanly to a single tunnel ingress.

### Options considered

1. **In-app web terminal (recommended).**
   Run a small web terminal (e.g. `ttyd`) in each workspace and expose it at
   `seko.pushprh.com/workspaces/<id>/terminal`. seko-ai reverse-proxies (incl. WebSocket) to
   the workspace's container on epyc, authorizing by workspace ownership. Rides the existing
   `seko` Cloudflare tunnel + Authelia SSO — **no new DNS/Cloudflare record**, nothing for the
   friend to install, works for every workspace.
   - Trade-off: browser terminal (great for `tmux` + `pi`; not VS Code Remote-SSH or `scp`).
   - Work: add `ttyd` to the workspace image (bound to localhost, spawning a `dev` login/tmux);
     publish its port; add a WebSocket-capable reverse proxy in seko-ai
     (`/workspaces/<id>/terminal`) that looks up the container's host:port from the DB and
     enforces ownership; a small UI "Open terminal" button. Consider `httpx-ws`/`websockets`
     for the proxy, or have Caddy proxy with seko-ai issuing a signed short-lived token.

2. **Native SSH via Cloudflare Access.**
   Real `ssh`, but each user installs `cloudflared` and adds
   `ProxyCommand cloudflared access ssh --hostname %h` to their SSH config, behind a
   Cloudflare Access app on a dedicated hostname. Supporting many *dynamic* workspaces cleanly
   needs either dynamic tunnel config per launch or a single SSH bastion that multiplexes —
   more moving parts, and friction for friends.

3. **Per-workspace SSH subdomain with dynamic Cloudflare management.**
   seko-ai provisions a DNS record + tunnel ingress per workspace via the Cloudflare API on
   launch (and tears it down on terminate). Most "productized," but requires a Cloudflare API
   token, dynamic `cloudflared` config reloads, and careful cleanup.

4. **Keep Tailscale-only SSH**, just document the Tailscale onboarding better.

### Recommendation
Option 1 (in-app web terminal) for public/no-install access, **plus** keep native
SSH-over-Tailscale for power users (VS Code Remote-SSH, `scp`).

### Notes
- The web UI itself still needs its one-time public Cloudflare record (orange CNAME
  `seko` → `<tunnel-UUID>.cfargotunnel.com`) for internet access — see
  `../psx-homelab/docs/seko-ai-runbook.md`. Workspace public access (this item) is separate.
