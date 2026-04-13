#!/bin/bash
# update.sh — Pull latest changes and redeploy autorrent
# Usage: sudo bash update.sh

set -e

BRANCH="claude/plan-torrent-downloader-C5bR9"
IMAGE="autorrent-autorrent"
CONTAINER="autorrent-autorrent-1"
PORT="8180:8000"
DATA_VOLUME="/mnt/shared_vol/websites/autorrent/data:/app/data"

echo "==> Pulling latest code from GitHub..."
git pull origin "$BRANCH"

echo "==> Building Docker image..."
docker build -t "$IMAGE" .

echo "==> Stopping and removing old container..."
docker stop "$CONTAINER" && docker rm "$CONTAINER"

echo "==> Starting new container..."
docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  -p "$PORT" \
  -v "$DATA_VOLUME" \
  "$IMAGE"

echo "==> Done! Container status:"
docker ps | grep "$CONTAINER"
