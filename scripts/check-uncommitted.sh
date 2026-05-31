#!/bin/bash
# Run before ending a session to catch forgotten changes
set -e
cd "$(git rev-parse --show-toplevel)"
STAGED=$(git diff --cached --name-only)
UNSTAGED=$(git diff --name-only)
UNTRACKED=$(git ls-files --others --exclude-standard)

if [ -z "$STAGED$UNSTAGED$UNTRACKED" ]; then
  echo "✓ Working tree clean — nothing uncommitted."
  exit 0
fi

echo "⚠ Uncommitted work found:"
[ -n "$STAGED" ]    && echo "\nStaged:\n$STAGED"
[ -n "$UNSTAGED" ]  && echo "\nUnstaged:\n$UNSTAGED"
[ -n "$UNTRACKED" ] && echo "\nUntracked:\n$UNTRACKED"
exit 1
