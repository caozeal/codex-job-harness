#!/bin/sh
# Install / refresh the harness from this repo into the Hermes profile.
#   repo  ->  ~/.hermes/codex-harness            (library, CODEX_HARNESS_HOME)
#   plugin ->  ~/.hermes/plugins/codex-worker    (Hermes loads this one)
#   cron   ->  ~/.hermes/scripts/codex_watch.py  (no_agent watcher entry)
# Idempotent: safe to re-run after every edit.
set -eu

REPO=$(cd "$(dirname "$0")" && pwd)
HOME_DIR=${HOME:?HOME is not set}
LIB="$HOME_DIR/.hermes/codex-harness"
PLUGIN="$HOME_DIR/.hermes/plugins/codex-worker"
CRON="$HOME_DIR/.hermes/scripts/codex_watch.py"

mkdir -p "$LIB" "$HOME_DIR/.hermes/plugins" "$HOME_DIR/.hermes/scripts"
rsync -a --delete --exclude .git --exclude __pycache__ "$REPO/" "$LIB/"
rm -rf "$PLUGIN"
cp -R "$LIB/plugin/codex-worker" "$PLUGIN"

cat > "$CRON" <<'PY'
"""Cron entry point: sweep codex jobs and print terminal notifications (empty = silence)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".hermes" / "codex-harness"))
os.environ.setdefault("CODEX_NOTIFY_MODE", "stdout")

import notify  # noqa: E402

raise SystemExit(notify.main())
PY

echo "installed:"
echo "  lib    $LIB"
echo "  plugin $PLUGIN"
echo "  cron   $CRON"
echo
echo "plugin code changed? reload it (tools load on the next session):"
echo "  hermes plugins disable codex-worker && hermes plugins enable codex-worker"
