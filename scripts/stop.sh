#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

# Remove only runtimes bearing this project's ownership label. Persistent data remains.
docker ps -aq --filter 'label=com.mumu-hermes.managed=true' | xargs -r docker rm -f
docker compose down
docker network ls -q --filter 'label=com.mumu-hermes.managed=true' | xargs -r docker network rm
