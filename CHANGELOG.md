# Changelog

seko-ai follows [Semantic Versioning](https://semver.org/). The public interface is
[`docs/configuration.md`](docs/configuration.md). While below 1.0, a breaking change to it
bumps the minor version; from 1.0 onward, the major version. Each release lists the
upgrade steps a deployment must take.

## 0.8.0

seko-ai no longer assumes any particular deployment.

### Breaking — upgrade notes

- These settings no longer have defaults and must be set: `SEKO_BASE_URL`,
  `SEKO_SESSION_SECRET`, `SEKO_OIDC_ISSUER`, `SEKO_OIDC_USERS_GROUP`,
  `SEKO_OIDC_ADMINS_GROUP`, `SEKO_LITELLM_BASE_URL`, `SEKO_LLM_PUBLIC_URL`,
  `SEKO_LLM_MODEL`.
- `SEKO_LLM_EMBEDDING_ENABLED` and `SEKO_LLM_IMAGE_GENERATION_ENABLED` now default to
  `false`. To keep that guidance, set them to `true` together with
  `SEKO_LLM_EMBEDDING_MODEL` + `SEKO_LLM_EMBEDDING_DIMENSION` and `SEKO_LLM_IMAGE_MODEL` +
  `SEKO_LLM_IMAGE_QUALITY_MODEL`, which are now required when their switch is on.
- `SEKO_ALERT_EMAIL_FROM` no longer has a default and is required when
  `SEKO_RESEND_API_KEY` is set. `SEKO_SERVICE_USAGE_ALIASES` defaults to empty.
- The guide's hosted-chat link now comes from `SEKO_CHAT_URL` (omitted when empty) and the
  MCP endpoint from `SEKO_LLM_MCP_URL` (derived from `SEKO_LLM_PUBLIC_URL` by default).
- The availability probe now runs in-process every `SEKO_STATUS_PROBE_INTERVAL` seconds
  (default 60). **Remove any external timer that runs `check-status`**; running both halves
  the effective failure threshold's duration.

### Added

- `seko-ai` subcommands: `serve` (default), `check-status`, `maintenance`, `backup`.
- Maintenance leases: `seko-ai maintenance start|end --owner TOKEN`, plus `--no-notify` and
  `--json`. An existing window whose message is the legacy `gpu-stack:<tokens>` marker is
  migrated to owners automatically.
- `seko-ai backup DEST`: consistent online SQLite backup.
- `docs/configuration.md`, `examples/compose.yaml`, and this changelog.

### Changed

- Operator commands log to stderr; stdout carries only the result.

## 0.7.1 and earlier

Released before this changelog existed; see the Git history.
