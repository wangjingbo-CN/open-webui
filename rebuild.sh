#!/usr/bin/env bash
set -e

IMAGE_NAME="open-webui-local:latest"
CONTAINER_NAME="open-webui"

docker build -t "$IMAGE_NAME" .

docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

docker run -d \
  -p 3000:8080 \
  -v open-webui:/app/backend/data \
  --name "$CONTAINER_NAME" \
  --restart always \
  "$IMAGE_NAME"

docker logs -f "$CONTAINER_NAME"
