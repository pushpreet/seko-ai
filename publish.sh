#!/usr/bin/env bash
# Sole manual release path for the seko-ai control-plane image.
# Prerequisite: authenticate once with `docker login ghcr.io`.
set -Eeuo pipefail

readonly IMAGE="ghcr.io/pushpreet/seko-ai"
readonly SOURCE_URL="https://github.com/pushpreet/seko-ai"
readonly DOCKER_BIN="${DOCKER_BIN:-docker}"
readonly GIT_BIN="${GIT_BIN:-git}"
readonly PYTHON_BIN="${PYTHON_BIN:-python3}"

die() {
  printf 'publish.sh: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

project_version() {
  "$PYTHON_BIN" - <<'PY'
import tomllib

with open("pyproject.toml", "rb") as project_file:
    print(tomllib.load(project_file)["project"]["version"])
PY
}

require_clean_worktree() {
  [[ -z "$("$GIT_BIN" status --porcelain=v1 --untracked-files=all)" ]] \
    || die "git worktree is dirty; commit or stash every change before publishing"
}

require_ghcr_login() {
  "$PYTHON_BIN" - <<'PY' || exit 1
import json
import os
from pathlib import Path

config_dir = Path(os.environ.get("DOCKER_CONFIG", Path.home() / ".docker"))
try:
    config = json.loads((config_dir / "config.json").read_text())
except (OSError, json.JSONDecodeError):
    raise SystemExit("publish.sh: run `docker login ghcr.io` before publishing")
if "ghcr.io" not in config.get("auths", {}):
    raise SystemExit("publish.sh: run `docker login ghcr.io` before publishing")
PY
}

require_unused_tag() {
  local ref="$1"
  local output
  if output="$("$DOCKER_BIN" buildx imagetools inspect "$ref" 2>&1)"; then
    die "refusing to overwrite existing image tag: $ref"
  fi
  case "$output" in
    *"manifest unknown"*|*"not found"*|*"Not Found"*) ;;
    *) die "could not verify that $ref is unused: $output" ;;
  esac
}

resolve_digest() {
  local ref="$1"
  local output digest
  output="$("$DOCKER_BIN" buildx imagetools inspect "$ref")"
  digest="$(printf '%s\n' "$output" | awk '$1 == "Digest:" { print $2; exit }')"
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || die "registry returned an invalid digest for $ref: ${digest:-<missing>}"
  printf '%s\n' "$digest"
}

main() {
  [[ "$#" -eq 0 ]] || die "version arguments are forbidden; edit pyproject.toml instead"
  cd "$(dirname "${BASH_SOURCE[0]}")"

  require_command "$DOCKER_BIN"
  require_command "$GIT_BIN"
  require_command "$PYTHON_BIN"
  require_command awk

  local version revision ref digest
  version="$(project_version)"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] \
    || die "invalid project version in pyproject.toml: $version"
  revision="$("$GIT_BIN" rev-parse --verify HEAD)"
  [[ "$revision" =~ ^[0-9a-f]{40}$ ]] || die "could not resolve the git revision"
  ref="${IMAGE}:${version}"

  require_clean_worktree
  require_ghcr_login
  require_unused_tag "$ref"

  ./tasks.sh check

  "$DOCKER_BIN" build \
    --label "org.opencontainers.image.source=${SOURCE_URL}" \
    --label "org.opencontainers.image.revision=${revision}" \
    --label "org.opencontainers.image.version=${version}" \
    --tag "$ref" \
    .
  "$DOCKER_BIN" push "$ref"

  digest="$(resolve_digest "$ref")"
  printf '%s@%s\n' "$ref" "$digest"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
