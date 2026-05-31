#!/usr/bin/env bash
# Build firmware locally and publish a GitHub Release.
#
# Usage:
#   ./scripts/release.sh [OPTIONS] [VERSION]
#
# Examples:
#   ./scripts/release.sh --yes              # bump patch, build, publish, push
#   ./scripts/release.sh --dry-run            # show plan only
#   ./scripts/release.sh --build-only         # build to out/, no tag/publish/push
#   ./scripts/release.sh v1.9.2 --no-push     # explicit version, skip git push
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

EXPECTED_ORIGIN='git@github.com:phamhanh/gaggimate.git'
GITHUB_REPO='phamhanh/gaggimate'
DEFAULT_BRANCH='master'

BUMP='patch'
YES=0
DRY_RUN=0
BUILD_ONLY=0
NO_PUSH=0
DISPLAY_ONLY=0
FORCE=0
EXPLICIT_VERSION=''

usage() {
  cat <<'EOF'
Usage: ./scripts/release.sh [OPTIONS] [VERSION]

Auto-version, build firmware locally, publish to GitHub Releases, and push refs.

Options:
  --patch          Bump patch version (default: v1.9.0 → v1.9.1)
  --minor          Bump minor version
  --major          Bump major version
  --yes            Skip confirmation prompt
  --dry-run        Print plan without making changes
  --build-only     Build to out/ only (no tag, publish, or push)
  --no-push        Tag, build, and publish; skip git push
  --display-only   Skip controller and display-headless builds
  --force          Replace an existing tag/release
  -h, --help       Show this help

VERSION            Explicit version override, e.g. v1.9.1

Requires: git, platformio, node/npm, and gh (for publish; see --build-only).

One-time setup:
  brew install gh && gh auth login
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --patch) BUMP='patch'; shift ;;
    --minor) BUMP='minor'; shift ;;
    --major) BUMP='major'; shift ;;
    --yes) YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --build-only) BUILD_ONLY=1; shift ;;
    --no-push) NO_PUSH=1; shift ;;
    --display-only) DISPLAY_ONLY=1; shift ;;
    --force) FORCE=1; shift ;;
    -h | --help)
      usage
      exit 0
      ;;
    v[0-9]*)
      EXPLICIT_VERSION="$1"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

die() {
  echo "Error: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

latest_semver_tag() {
  git tag -l 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1
}

bump_version() {
  local current="$1"
  local kind="$2"
  local major minor patch

  current="${current#v}"
  IFS='.' read -r major minor patch <<< "$current"

  case "$kind" in
    major)
      major=$((major + 1))
      minor=0
      patch=0
      ;;
    minor)
      minor=$((minor + 1))
      patch=0
      ;;
    patch)
      patch=$((patch + 1))
      ;;
    *)
      die "Unknown bump kind: $kind"
      ;;
  esac

  printf 'v%s.%s.%s' "$major" "$minor" "$patch"
}

preflight() {
  require_cmd git
  require_cmd platformio
  require_cmd node
  require_cmd npm

  if [[ "$BUILD_ONLY" -eq 0 && "$DRY_RUN" -eq 0 ]]; then
    require_cmd gh
    gh auth status >/dev/null 2>&1 || die "gh is not authenticated — run: gh auth login"
  fi

  local origin
  origin="$(git remote get-url origin)"
  [[ "$origin" == "$EXPECTED_ORIGIN" ]] || die "origin must be $EXPECTED_ORIGIN (got: $origin)"

  git fetch origin --tags

  if [[ "$DRY_RUN" -eq 0 && -n "$(git status --porcelain)" ]]; then
    git status --short >&2
    die "Working tree is dirty — commit or stash changes before releasing (prevents vX.Y.Z-dirty in firmware)"
  fi
}

resolve_version() {
  if [[ -n "$EXPLICIT_VERSION" ]]; then
    VERSION="$EXPLICIT_VERSION"
    return
  fi

  local latest
  latest="$(latest_semver_tag)"
  [[ -n "$latest" ]] || die "No semver tag found matching vX.Y.Z — pass an explicit VERSION"

  VERSION="$(bump_version "$latest" "$BUMP")"
}

validate_version() {
  [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Invalid version format: $VERSION (expected vX.Y.Z)"

  if git rev-parse "$VERSION" >/dev/null 2>&1; then
    [[ "$FORCE" -eq 1 ]] || die "Tag $VERSION already exists (use --force to replace)"
  fi
}

build_args() {
  BUILD_ARGS=(--version "$VERSION")
  if [[ "$DISPLAY_ONLY" -eq 1 ]]; then
    BUILD_ARGS+=(--display-only)
  fi
}

print_plan() {
  cat <<EOF
Release plan
============
Version:       $VERSION
Bump:          $(if [[ -n "$EXPLICIT_VERSION" ]]; then echo "explicit ($EXPLICIT_VERSION)"; else echo "$BUMP from $(latest_semver_tag)"; fi)
Build only:    $([[ "$BUILD_ONLY" -eq 1 ]] && echo yes || echo no)
Display only:  $([[ "$DISPLAY_ONLY" -eq 1 ]] && echo yes || echo no)
Dry run:       $([[ "$DRY_RUN" -eq 1 ]] && echo yes || echo no)
Push refs:     $([[ "$BUILD_ONLY" -eq 1 || "$NO_PUSH" -eq 1 || "$DRY_RUN" -eq 1 ]] && echo no || echo yes)
Force:         $([[ "$FORCE" -eq 1 ]] && echo yes || echo no)

Steps:
EOF
  if [[ "$BUILD_ONLY" -eq 1 ]]; then
    echo "  1. Run ./scripts/build-firmware.sh ${BUILD_ARGS[*]}"
    return
  fi

  echo "  1. git tag $VERSION (locally, before build)"
  echo "  2. Run ./scripts/build-firmware.sh ${BUILD_ARGS[*]}"
  echo "  3. gh release create/upload $VERSION out/*.bin out/version.txt"
  if [[ "$NO_PUSH" -eq 0 ]]; then
    echo "  4. git push origin $DEFAULT_BRANCH"
    echo "  5. git push origin $VERSION"
  fi
}

confirm() {
  if [[ "$YES" -eq 1 || "$DRY_RUN" -eq 1 || "$BUILD_ONLY" -eq 1 ]]; then
    return 0
  fi
  read -r -p "Proceed with release $VERSION? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || die "Aborted"
}

tag_version() {
  if git rev-parse "$VERSION" >/dev/null 2>&1; then
    if [[ "$FORCE" -eq 1 ]]; then
      git tag -d "$VERSION"
    fi
  fi
  git tag "$VERSION"
}

publish_release() {
  local assets=(out/*.bin out/version.txt)
  local release_exists=0

  if gh release view "$VERSION" --repo "$GITHUB_REPO" >/dev/null 2>&1; then
    release_exists=1
  fi

  if [[ "$release_exists" -eq 0 ]]; then
    gh release create "$VERSION" "${assets[@]}" \
      --repo "$GITHUB_REPO" \
      --title "Release $VERSION" \
      --generate-notes \
      --latest
  else
    gh release upload "$VERSION" "${assets[@]}" \
      --repo "$GITHUB_REPO" \
      --clobber
  fi
}

push_refs() {
  git push origin "$DEFAULT_BRANCH"
  git push origin "$VERSION"
}

rollback_hint() {
  echo ""
  echo "Release failed after tagging. To roll back locally:"
  echo "  git tag -d $VERSION"
  if [[ "$FORCE" -eq 0 ]]; then
    echo "Remote tag was not pushed yet."
  fi
}

preflight
resolve_version
validate_version
build_args
print_plan
confirm

if [[ "$DRY_RUN" -eq 1 ]]; then
  exit 0
fi

if [[ "$BUILD_ONLY" -eq 1 ]]; then
  ./scripts/build-firmware.sh "${BUILD_ARGS[@]}"
  exit 0
fi

TAGGED=0
trap 'if [[ "$TAGGED" -eq 1 ]]; then rollback_hint; fi' ERR

tag_version
TAGGED=1

./scripts/build-firmware.sh "${BUILD_ARGS[@]}"

publish_release

if [[ "$NO_PUSH" -eq 0 ]]; then
  push_refs
fi

trap - ERR

REMOTE="https://github.com/$GITHUB_REPO"
echo ""
echo "Release $VERSION published."
echo "Releases: $REMOTE/releases"
echo "Latest OTA: $REMOTE/releases/latest"
