#!/usr/bin/env bash
#
# check_release_bump_files.sh — assert a release-bump PR only touches the files
# a version bump is supposed to touch.
#
# The "Prepare release" workflow (and its local twin scripts/bump_version.py)
# edits exactly three files:
#
#   * app/pubspec.yaml                 (version: line — the source of truth)
#   * metadata/com.nexanote.app.yml    (F-Droid build entry + Current* pointers)
#   * CHANGELOG.md                     (rolls Unreleased into ## vX.Y.Z)
#
# A release-bump PR that also carries an unrelated source/UI/backend change is
# almost always a mistake: those changes belong in their own PR so the release
# commit stays a clean, reviewable "version only" diff (and so it is obvious
# nothing in the build changed between the tagged commit and its parent). This
# guard fails such a PR with the offending paths named.
#
# It is intentionally scoped: CI only runs it for PRs whose branch starts with
# `release/` (the branch the prepare workflow pushes). It is read-only, makes
# no network calls, and needs no secrets.
#
# Usage:
#
#   # Explicit list (one path per line) on stdin:
#   git diff --name-only <base>..HEAD | scripts/check_release_bump_files.sh
#
#   # Or as arguments:
#   scripts/check_release_bump_files.sh app/pubspec.yaml CHANGELOG.md
#
# The allowed set also permits app/pubspec.lock (a dependency refresh may ride
# along) and docs/release-notes/** (longer GitHub-Release notes), since both are
# part of cutting a release. Anything else is reported and fails.

set -uo pipefail

# Collect candidate paths from arguments, else from stdin.
paths=()
if [ "$#" -gt 0 ]; then
  paths=("$@")
else
  while IFS= read -r line; do
    [ -n "$line" ] && paths+=("$line")
  done
fi

if [ "${#paths[@]}" -eq 0 ]; then
  echo "No changed files provided; nothing to check."
  exit 0
fi

# Returns 0 if the given path is allowed in a release-bump PR.
is_allowed() {
  case "$1" in
    app/pubspec.yaml|app/pubspec.lock|\
    metadata/com.nexanote.app.yml|\
    CHANGELOG.md|\
    docs/release-notes/*)
      return 0 ;;
    *)
      return 1 ;;
  esac
}

offenders=()
for p in "${paths[@]}"; do
  is_allowed "$p" || offenders+=("$p")
done

if [ "${#offenders[@]}" -eq 0 ]; then
  echo "OK: release-bump PR touches only allowed version files (${#paths[@]} file(s))."
  exit 0
fi

{
  echo "ERROR: this looks like a release-bump PR, but it changes files outside"
  echo "the allowed version-bump set:"
  echo
  for p in "${offenders[@]}"; do
    echo "  - $p"
  done
  echo
  echo "A release bump should be a clean, version-only diff. Allowed files:"
  echo "  - app/pubspec.yaml, app/pubspec.lock"
  echo "  - metadata/com.nexanote.app.yml"
  echo "  - CHANGELOG.md"
  echo "  - docs/release-notes/**"
  echo
  echo "Move the unrelated change to its own PR, or — if it genuinely belongs"
  echo "with the release — widen the allowlist in scripts/check_release_bump_files.sh."
} >&2
exit 1
