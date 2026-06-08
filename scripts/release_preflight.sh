#!/usr/bin/env bash
#
# release_preflight.sh — verify a release tag matches app/pubspec.yaml *before*
# (or while) publishing it. Pure bash; does not need Flutter or Dart.
#
# NexaNote drives every published version from a single `vX.Y.Z` git tag. The
# tag's versionName must equal the `versionName` part of app/pubspec.yaml's
# `version: X.Y.Z+versionCode` line, so the GitHub Release, the APK, the Docker
# image tag, and the in-app About screen never disagree. This script is the
# single place that check lives: the "Verify tag matches pubspec version" step
# in .github/workflows/android-release.yml runs THIS script, so CI and a
# contributor's pre-tag check fail with the exact same wording.
#
# Usage:
#
#   ./scripts/release_preflight.sh v1.1.0
#   ./scripts/release_preflight.sh 1.1.0 --pubspec path/to/pubspec.yaml
#
# Options:
#
#   --pubspec PATH   Read pubspec.yaml from PATH (default: ./app/pubspec.yaml).
#   -h, --help       Show this help.
#
# What it does:
#
#   1. Validates the tag is a clean `vX.Y.Z` (the leading `v` is optional on
#      input; the displayed form always carries it).
#   2. Reads `version: X.Y.Z+versionCode` from app/pubspec.yaml.
#   3. Compares the tag's X.Y.Z against the pubspec versionName. On a mismatch
#      (or a malformed tag) it emits a multi-line ERROR that names the pushed
#      tag, the actual pubspec version, and the exact fix — then exits non-zero.
#   4. On success it prints NEXANOTE_VERSION_NAME / NEXANOTE_VERSION_CODE for CI
#      capture, an `OK:` confirmation, and the next safe commands.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PUBSPEC="$REPO_ROOT/app/pubspec.yaml"
TAG=""

usage() {
  cat <<'EOF'
Usage: scripts/release_preflight.sh <tag> [--pubspec PATH]

  <tag>        Release tag, e.g. v1.1.0 (with or without the leading v).
  --pubspec    Read pubspec.yaml from PATH (default: ./app/pubspec.yaml).
  -h, --help   Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --pubspec)
      [ "$#" -ge 2 ] || { echo "ERROR: --pubspec needs a path." >&2; exit 64; }
      PUBSPEC="$2"; shift 2 ;;
    --)
      shift
      if [ "$#" -gt 0 ] && [ -z "$TAG" ]; then TAG="$1"; shift; fi
      ;;
    -*)
      printf 'ERROR: unknown option: %s\n\n' "$1" >&2
      usage >&2
      exit 64
      ;;
    *)
      if [ -z "$TAG" ]; then
        TAG="$1"
      else
        printf 'ERROR: unexpected argument: %s\n\n' "$1" >&2
        usage >&2
        exit 64
      fi
      shift
      ;;
  esac
done

if [ -z "$TAG" ]; then
  printf 'ERROR: missing tag argument.\n\n' >&2
  usage >&2
  exit 64
fi

# Trim surrounding whitespace.
TAG="${TAG#"${TAG%%[![:space:]]*}"}"
TAG="${TAG%"${TAG##*[![:space:]]}"}"

# Display form always carries the leading v so messages match what a user types.
display_tag="$TAG"
[[ "$display_tag" != v* ]] && display_tag="v$display_tag"
tag_version="${display_tag#v}"

# NexaNote tags are plain semver X.Y.Z (no pre-release suffix).
if [[ ! "$tag_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  cat >&2 <<EOF
::error::Malformed release tag "$display_tag".
Expected vX.Y.Z (three numeric components), e.g. v1.1.0.
EOF
  exit 1
fi

if [ ! -f "$PUBSPEC" ]; then
  echo "::error::pubspec.yaml not found at $PUBSPEC" >&2
  exit 1
fi

# Read `version: X.Y.Z+versionCode` -> versionName + versionCode.
pubspec_full="$(sed -n 's/^version:[[:space:]]*//p' "$PUBSPEC" | head -n1 | tr -d '[:space:]')"
if [ -z "$pubspec_full" ]; then
  echo "::error::pubspec.yaml at $PUBSPEC has no \`version:\` line." >&2
  exit 1
fi
pubspec_version="${pubspec_full%%+*}"
if [[ "$pubspec_full" == *+* ]]; then
  pubspec_code="${pubspec_full#*+}"
else
  pubspec_code=""
fi

if [ "$tag_version" != "$pubspec_version" ]; then
  cat >&2 <<EOF
::error::Release tag/pubspec version mismatch — refusing to publish.
  current tag version:     $display_tag (the tag you pushed)
  current pubspec version: $pubspec_version (app/pubspec.yaml)
Expected next action: tag the commit on main where app/pubspec.yaml already reads $tag_version, or run the Prepare release workflow to bump app/pubspec.yaml to $tag_version first, then re-create tag $display_tag.
Do not move an existing published tag.
EOF
  exit 1
fi

# Success: KEY=VALUE first so CI can capture them, then the friendly summary.
printf 'NEXANOTE_VERSION_NAME=%s\n' "$pubspec_version"
printf 'NEXANOTE_VERSION_CODE=%s\n' "$pubspec_code"
printf 'OK: %s matches app/pubspec.yaml version %s.\n' "$display_tag" "$pubspec_full"

cat <<EOF

Next safe commands (run from a clean checkout of main):
  git checkout main
  git pull origin main
  git tag $display_tag
  git push origin $display_tag

Then watch GitHub Actions: the Android and Docker workflows build and publish
the release artifacts for $display_tag.
EOF
