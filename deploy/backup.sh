#!/usr/bin/env bash
# Nightly copy of the game files (state, server IDs, history logs). Keeps 30 days.
set -euo pipefail
APP=/opt/statecraft
DEST=$APP/backups/$(date +%Y%m%d-%H%M)
mkdir -p "$DEST"
cp -p "$APP"/*.json "$APP"/*.jsonl "$DEST"/ 2>/dev/null || true
find "$APP/backups" -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf {} +
