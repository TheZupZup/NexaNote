#!/usr/bin/env python3
"""Bump NexaNote's version across every release artifact.

One ``vX.Y.Z`` git tag drives all of NexaNote's visible versions. This script
keeps the *source* files that the tag is checked against in sync, so cutting a
release is just: run this script, commit, tag ``vX.Y.Z``, push the tag.

From a single ``(versionName, versionCode)`` pair it updates:

  * ``app/pubspec.yaml``              -> ``version: X.Y.Z+versionCode``.
    Android's ``versionName``/``versionCode`` follow this automatically via the
    Flutter Gradle plugin, so the tag, the APK, and the in-app About screen all
    stay in lockstep.
  * ``metadata/com.nexanote.app.yml`` -> an F-Droid build entry
    (``versionName``, ``versionCode``, ``commit: vX.Y.Z``) plus the
    ``CurrentVersion`` / ``CurrentVersionCode`` pointers.
  * ``CHANGELOG.md``                  -> rolls the ``## Unreleased`` section into
    a ``## vX.Y.Z`` entry and leaves a fresh ``## Unreleased`` placeholder.

It never creates a git tag unless you pass ``--tag``, and never adds Google Play
or any proprietary tooling — the output stays F-Droid- and Obtainium-friendly.

Usage:
    python scripts/bump_version.py 1.0.2 3
    python scripts/bump_version.py 1.0.2 3 --no-changelog
    python scripts/bump_version.py 1.0.2 3 --tag   # also runs `git tag v1.0.2`
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

#: Repo root, derived from this file's location (scripts/ -> repo root).
ROOT = Path(__file__).resolve().parents[1]

VERSION_NAME_RE = re.compile(r"^\d+\.\d+\.\d+$")


# --- validation ------------------------------------------------------------

def parse_version_name(value: str) -> str:
    """Return *value* if it is a clean semver ``X.Y.Z``, else raise."""
    if not VERSION_NAME_RE.match(value):
        raise ValueError(
            f"versionName must look like X.Y.Z (got {value!r})"
        )
    return value


def parse_version_code(value) -> int:
    """Return *value* as a positive int, else raise."""
    try:
        code = int(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"versionCode must be an integer (got {value!r})")
    if code < 1:
        raise ValueError(f"versionCode must be >= 1 (got {code})")
    return code


# --- pubspec.yaml ----------------------------------------------------------

def read_pubspec_version_code(text: str):
    """Return the current versionCode in a pubspec, or None if absent."""
    m = re.search(r"^version:\s*\d+\.\d+\.\d+\+(\d+)", text, re.MULTILINE)
    return int(m.group(1)) if m else None


def bump_pubspec(text: str, version_name: str, version_code: int) -> str:
    """Rewrite the ``version:`` line to ``X.Y.Z+versionCode``."""
    new_text, n = re.subn(
        r"^version:\s*.*$",
        f"version: {version_name}+{version_code}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n == 0:
        raise ValueError("no `version:` line found in pubspec.yaml")
    return new_text


# --- F-Droid metadata ------------------------------------------------------

def _append_build(lines, version_name: str, version_code: int):
    """Append a new F-Droid build entry to the ``Builds:`` list.

    Idempotent: if an entry already declares this ``versionCode`` the list is
    left untouched. The new entry mirrors the existing format (including the
    ``gradle: - yes`` flavor marker that F-Droid expects), which is why this is
    done with line edits rather than a YAML round-trip — PyYAML would rewrite
    the bare ``yes`` as ``true`` and reorder keys.
    """
    start = None
    for i, line in enumerate(lines):
        if line.startswith("Builds:"):
            start = i
            break
    if start is None:
        return lines

    # The Builds block runs until the next top-level (non-indented) key.
    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if line and not line[0].isspace():
            end = j
            break

    block = lines[start + 1:end]
    for line in block:
        if re.match(rf"^\s*versionCode:\s*{version_code}\s*$", line):
            return lines  # already present — keep the list stable

    # Insert after the last non-blank line of the block (before the blank line
    # that separates Builds: from the next section).
    insert_at = end
    while insert_at - 1 > start and lines[insert_at - 1].strip() == "":
        insert_at -= 1

    entry = [
        f"  - versionName: {version_name}",
        f"    versionCode: {version_code}",
        f"    commit: v{version_name}",
        "    subdir: app",
        "    gradle:",
        "      - yes",
    ]
    return lines[:insert_at] + entry + lines[insert_at:]


def _update_current(lines, version_name: str, version_code: int):
    """Update (or add) the ``CurrentVersion`` / ``CurrentVersionCode`` pointers."""
    out = []
    saw_name = saw_code = False
    for line in lines:
        if re.match(r"^CurrentVersion:\s", line):
            out.append(f"CurrentVersion: {version_name}")
            saw_name = True
        elif re.match(r"^CurrentVersionCode:\s", line):
            out.append(f"CurrentVersionCode: {version_code}")
            saw_code = True
        else:
            out.append(line)
    if not saw_name:
        out.append(f"CurrentVersion: {version_name}")
    if not saw_code:
        out.append(f"CurrentVersionCode: {version_code}")
    return out


def bump_fdroid_metadata(text: str, version_name: str, version_code: int) -> str:
    """Add the build entry and update the current-version pointers."""
    lines = text.splitlines()
    lines = _append_build(lines, version_name, version_code)
    lines = _update_current(lines, version_name, version_code)
    result = "\n".join(lines)
    if text.endswith("\n"):
        result += "\n"
    return result


# --- CHANGELOG.md ----------------------------------------------------------

def bump_changelog(text: str, version_name: str) -> str:
    """Roll the ``## Unreleased`` section into ``## vX.Y.Z``.

    Keeps any subtitle on the Unreleased heading (e.g. ``— Sync reliability``)
    as the release subtitle and drops a fresh ``## Unreleased`` placeholder
    above it. If there is no Unreleased section the changelog is left unchanged.
    """
    m = re.search(r"^## Unreleased(.*)$", text, re.MULTILINE)
    if not m:
        return text
    subtitle = m.group(1)
    placeholder = (
        "## Unreleased\n\n"
        "_Nothing yet._\n\n"
        "---\n\n"
    )
    released_heading = f"## v{version_name}{subtitle}"
    start, end = m.span()
    return text[:start] + placeholder + released_heading + text[end:]


# --- orchestration ---------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="bump_version.py",
        description="Bump NexaNote's version across pubspec, F-Droid metadata, "
                    "and the changelog. Does not tag git unless --tag is given.",
    )
    parser.add_argument("version_name", help="semver versionName, e.g. 1.0.2")
    parser.add_argument(
        "version_code",
        help="monotonically increasing integer versionCode, e.g. 3",
    )
    parser.add_argument(
        "--no-changelog",
        action="store_true",
        help="do not touch CHANGELOG.md",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow a versionCode that is not greater than the current one",
    )
    parser.add_argument(
        "--tag",
        action="store_true",
        help="also create the git tag vX.Y.Z (off by default)",
    )
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="repository root (defaults to this script's repo)",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        version_name = parse_version_name(args.version_name)
        version_code = parse_version_code(args.version_code)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    root = Path(args.root).resolve()
    pubspec_path = root / "app" / "pubspec.yaml"
    metadata_path = root / "metadata" / "com.nexanote.app.yml"
    changelog_path = root / "CHANGELOG.md"

    if not pubspec_path.exists():
        print(f"error: {pubspec_path} not found", file=sys.stderr)
        return 2

    pubspec_text = pubspec_path.read_text()

    # Enforce the monotonic versionCode rule (F-Droid/Android require it).
    current_code = read_pubspec_version_code(pubspec_text)
    if current_code is not None and version_code < current_code and not args.force:
        print(
            f"error: versionCode {version_code} is lower than the current "
            f"{current_code}; versionCode must increase. Use --force to override.",
            file=sys.stderr,
        )
        return 2

    # pubspec.yaml (single source of truth for the app version).
    pubspec_path.write_text(bump_pubspec(pubspec_text, version_name, version_code))
    print(f"updated {pubspec_path.relative_to(root)} -> {version_name}+{version_code}")

    # F-Droid metadata.
    if metadata_path.exists():
        metadata_path.write_text(
            bump_fdroid_metadata(
                metadata_path.read_text(), version_name, version_code
            )
        )
        print(
            f"updated {metadata_path.relative_to(root)} "
            f"-> build {version_name} ({version_code}), commit v{version_name}"
        )
    else:
        print(f"skipped F-Droid metadata ({metadata_path} not found)")

    # CHANGELOG.md placeholder.
    if not args.no_changelog and changelog_path.exists():
        before = changelog_path.read_text()
        after = bump_changelog(before, version_name)
        if after != before:
            changelog_path.write_text(after)
            print(
                f"updated {changelog_path.relative_to(root)} "
                f"-> rolled Unreleased into v{version_name}"
            )
        else:
            print("CHANGELOG.md: no `## Unreleased` section, left unchanged")

    # Git tag only on explicit request.
    tag = f"v{version_name}"
    if args.tag:
        subprocess.run(["git", "-C", str(root), "tag", tag], check=True)
        print(f"created git tag {tag}")
    else:
        print()
        print("Next steps:")
        print(f"  git commit -am 'release: NexaNote {tag}'")
        print(f"  git tag {tag}")
        print(f"  git push && git push origin {tag}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
