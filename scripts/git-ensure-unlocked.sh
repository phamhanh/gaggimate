#!/usr/bin/env bash
# Remove a stale .git/index.lock when no git process is using this repo.
# Safe to run before git add/commit from scripts or agents.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
LOCK="$ROOT/.git/index.lock"

[[ -f "$LOCK" ]] || exit 0

if pgrep -lf git 2>/dev/null | grep -qF "$ROOT"; then
  echo "git-ensure-unlocked: $LOCK exists and a git process is still using this repo — not removing." >&2
  exit 1
fi

echo "git-ensure-unlocked: removing stale $LOCK (no active git in repo)" >&2
rm -f "$LOCK"
