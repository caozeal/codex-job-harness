#!/bin/sh
# Install the coord harness and plugin without deleting its runtime queue.
set -eu

REPO=$(cd "$(dirname "$0")" && pwd)
PROFILE="${HOME:?HOME is not set}/.hermes/profiles/coord"
LIB="$PROFILE/codex-harness"
PLUGIN="$PROFILE/plugins/codex-worker"

mkdir -p "$LIB" "$PLUGIN"
rsync -a --delete --exclude '/codex-jobs/' --exclude '__pycache__/' "$REPO/" "$LIB/"
rsync -a --delete --exclude '__pycache__/' "$REPO/plugin/codex-worker/" "$PLUGIN/"
printf 'Installed coord harness: %s\nInstalled plugin: %s\n' "$LIB" "$PLUGIN"
