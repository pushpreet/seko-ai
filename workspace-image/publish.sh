#!/usr/bin/env bash
# Build and publish the seko workspace image to GHCR.
#
# Requires: docker login ghcr.io
# After the first push, make the package public in GitHub:
#   Package page -> Package settings -> Danger Zone -> Change visibility -> Public
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

IMAGE="ghcr.io/pushpreet/seko-workspace"
TAG="${1:-}"

if [ -z "${TAG}" ]; then
  if [ -s VERSION ]; then
    TAG="$(tr -d '[:space:]' < VERSION)"
  else
    TAG="latest"
  fi
fi

BUILD_ARGS=()
if [ -n "${PI_VERSION:-}" ]; then
  BUILD_ARGS+=(--build-arg "PI_VERSION=${PI_VERSION}")
fi
if [ -n "${NODE_MAJOR:-}" ]; then
  BUILD_ARGS+=(--build-arg "NODE_MAJOR=${NODE_MAJOR}")
fi

echo "Building ${IMAGE}:${TAG}"
docker build "${BUILD_ARGS[@]}" -t "${IMAGE}:${TAG}" .

echo "Tagging ${IMAGE}:latest"
docker tag "${IMAGE}:${TAG}" "${IMAGE}:latest"

echo "Pushing ${IMAGE}:${TAG}"
docker push "${IMAGE}:${TAG}"

echo "Pushing ${IMAGE}:latest"
docker push "${IMAGE}:latest"
