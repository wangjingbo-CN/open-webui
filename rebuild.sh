#!/usr/bin/env bash
set -e

IMAGE_NAME="open-webui-local:latest"
CONTAINER_NAME="open-webui"

docker build -t "$IMAGE_NAME" .

docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

docker run -d \
  -p 3000:8080 \
  --add-host=host.docker.internal:host-gateway \
  --build-arg HTTP_PROXY=http://host.docker.internal:7897 \
  --build-arg HTTPS_PROXY=http://host.docker.internal:7897 \
  --build-arg http_proxy=http://host.docker.internal:7897 \
  --build-arg https_proxy=http://host.docker.internal:7897 \
  -v open-webui:/app/backend/data \
  --name "$CONTAINER_NAME" \
  --restart always \
  "$IMAGE_NAME"

docker logs -f "$CONTAINER_NAME"
