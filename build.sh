#!/usr/bin/env sh
set -e

(
  cd frontend
  npm run lint
  npm run build
)

docker image prune -f && docker container prune -f
docker build -t axiomslice .
echo "✅ Build complete."
