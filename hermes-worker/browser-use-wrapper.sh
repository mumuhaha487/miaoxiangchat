#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${BU_CDP_URL:-}" && -z "${BU_CDP_WS:-}" ]]; then
  websocket_url="$(
    curl -fsS --noproxy '*' "${BU_CDP_URL%/}/json/version" 2>/dev/null \
      | grep -o 'ws://[^" ]*' \
      | head -n 1 \
      || true
  )"
  if [[ -n "$websocket_url" ]]; then
    export BU_CDP_WS="$websocket_url"
    unset BU_CDP_URL
  fi
fi

exec uvx browser-use "$@"
