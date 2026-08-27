#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "$project_dir" || "$project_dir" == "/" ]]; then
  echo "Refusing to clean an unsafe project path" >&2
  exit 1
fi

find "$project_dir" -mindepth 1 -type d -name .github -prune -exec rm -rf -- {} +
find "$project_dir" -mindepth 1 -type f -name .gitignore -delete

