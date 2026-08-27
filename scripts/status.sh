#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

docker compose ps
docker ps --filter 'label=com.mumu-hermes.managed=true' --format 'table {{.Names}}\t{{.Status}}\t{{.RunningFor}}'
