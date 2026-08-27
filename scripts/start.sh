#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

if [[ ! -f .env ]]; then
  bash scripts/initialize.sh
fi

chmod 600 .env
mkdir -p data/users docs vendor
chmod 700 data/users
bash scripts/clean-sync-metadata.sh

docker compose --profile build-only build browser-runtime-image hermes-worker-image
docker compose build proxy-bridge backend frontend
docker compose up -d proxy-bridge backend frontend
docker compose ps
bash scripts/clean-sync-metadata.sh
