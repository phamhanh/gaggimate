#!/bin/bash
# Usage: ./scripts/release.sh v1.9.0
set -e
VERSION=${1:?Usage: release.sh <version>}
git tag "$VERSION"
git push origin "$VERSION"
REMOTE=$(git remote get-url origin | sed 's/git@github.com:/https:\/\/github.com\//' | sed 's/\.git$//')
echo "Release $VERSION pushed."
echo "Track build: $REMOTE/actions"
echo "Releases: $REMOTE/releases"
