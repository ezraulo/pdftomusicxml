#!/usr/bin/env bash
# Start the app inside a codespace: use the published image if it can be pulled,
# otherwise build it here (10-15 minutes the first time).
set -euo pipefail
cd "$(dirname "$0")/.."
if ! docker compose pull --quiet; then
  echo "Could not pull the published image; building it instead (10-15 minutes)..."
  docker compose build
fi
docker compose up -d
for _ in $(seq 1 60); do
  if curl -sf http://localhost:8080/healthz >/dev/null; then
    echo "The sheet music converter is running: open port 8080 in the Ports tab."
    exit 0
  fi
  sleep 2
done
echo "The app did not answer yet; see: docker compose logs" >&2
