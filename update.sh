#!/bin/bash
set -e

echo "Pulling latest AutoRrent image..."
docker pull ghcr.io/parabyte-ca/autorrent:latest

echo "Restarting container..."
docker compose up -d

echo "Done. AutoRrent is up to date."
docker compose ps autorrent
