#!/usr/bin/env bash
# Build and publish the seko-ai control-plane image to GHCR.
#
# Requires: docker login ghcr.io
# After the first push, make the package public (or keep private and give core-infra a
# read token): GitHub package settings -> visibility.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

IMAGE="ghcr.io/pushpreet/seko-ai"
TAG="${1:-}"

if [ -z "${TAG}" ]; then
  TAG="$(python3 -c 'import tomllib;print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')"
fi

echo "Building ${IMAGE}:${TAG}"
docker build -t "${IMAGE}:${TAG}" -t "${IMAGE}:latest" .

echo "Pushing ${IMAGE}:${TAG} and :latest"
docker push "${IMAGE}:${TAG}"
docker push "${IMAGE}:latest"
