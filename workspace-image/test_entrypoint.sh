#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

scripts=(entrypoint.sh publish.sh test_entrypoint.sh)

echo "==> bash syntax checks"
for script in "${scripts[@]}"; do
  bash -n "${script}"
done

if command -v shellcheck >/dev/null 2>&1; then
  echo "==> shellcheck"
  shellcheck "${scripts[@]}"
else
  echo "==> shellcheck not installed; skipping"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "==> docker not installed; skipping docker smoke tests"
  exit 0
fi

if ! docker info >/dev/null 2>&1; then
  echo "==> docker daemon unavailable; skipping docker smoke tests"
  exit 0
fi

BUILT_IMAGE=0
cleanup() {
  if [ "${BUILT_IMAGE}" = "1" ]; then
    docker image rm seko-workspace:test >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "==> docker build"
docker build -t seko-workspace:test .
BUILT_IMAGE=1

echo "==> entrypoint smoke test"
sample_key='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE5vdEFGb3JSZWFsQXV0aEJ1dEZpbmVGb3JXaXJldGVzdA== test@example'
docker run --rm \
  -e SEKO_AUTHORIZED_KEYS="${sample_key}" \
  -e LLM_BASE_URL="https://llm.example.test/v1" \
  -e LLM_API_KEY="test-key" \
  -e LLM_MODEL="test-model" \
  seko-workspace:test \
  bash -lc 'set -euo pipefail
    test "$(cat /home/dev/.ssh/authorized_keys)" = "${SEKO_AUTHORIZED_KEYS}"
    test -s /etc/ssh/ssh_host_ed25519_key
    test -s /home/dev/.config/seko/llm.env
    grep -q "OPENAI_BASE_URL" /home/dev/.config/seko/llm.env
    grep -q "llm.env" /etc/profile.d/seko-llm.sh
    grep -q "OPENAI_BASE_URL" /etc/environment
    # both harnesses present on PATH
    command -v pi >/dev/null
    command -v omp >/dev/null
    # pi + omp harness config installed into the mounted home
    test -f /home/dev/.pi/agent/extensions/local-llm.ts
    test -f /home/dev/.omp/agent/extensions/local-llm.ts
    test -f /home/dev/.omp/agent/config.yml
    grep -q "local/test-model" /home/dev/.omp/agent/config.yml
    /usr/sbin/sshd -t
  '

echo "==> smoke tests passed"
