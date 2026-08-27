#!/usr/bin/env bash
set -euo pipefail

mkdir -p "$PROFILE_DIR" "$PROFILE_DIR/Downloads"
rm -f "$PROFILE_DIR/SingletonLock" "$PROFILE_DIR/SingletonSocket" "$PROFILE_DIR/SingletonCookie"

Xvfb "$DISPLAY" -screen 0 1440x900x24 -ac +extension RANDR &
openbox-session &
x11vnc -display "$DISPLAY" -forever -shared -nopw -rfbport 5900 -listen 0.0.0.0 -noxdamage -nocursorpos &

exec node /runtime/server.mjs
