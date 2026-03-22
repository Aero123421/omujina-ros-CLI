#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_NAME="${IMAGE_NAME:-mujina-assist-test}"

docker build -f "$ROOT_DIR/Dockerfile.test" -t "$IMAGE_NAME" "$ROOT_DIR"
docker run --rm "$IMAGE_NAME"
