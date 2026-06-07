"""Tests for scripts/bump_version.py — the single-source version bumper.

These exercise the pure text transforms (so they need neither Flutter nor a git
checkout) plus one end-to-end run against a temporary copy of the real files.
They lock in the contract the release model depends on: one (versionName,
versionCode) pair updates the pubspec, the F-Droid metadata, and the changelog
consistently, the F-Droid `gradle: - yes` flavor marker survives, and no git
tag is created unless asked for.
"""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bump_version.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bump = _load_module()


# Representative slices of the real files, kept inline so the unit tests do not
# depend on the live files' current version numbers.
PUBSPEC = """name: nexanote
description: Open-source, privacy-friendly note-taking app
publish_to: 'none'
version: 1.0.0+2

environment:
  sdk: '>=3.0.0 <4.0.0'
"""

METADATA = """License: MPL-2.0

AutoName: NexaNote

RepoType: git
Repo: https://github.com/TheZupZup/NexaNote

Builds:
  - versionName: 1.0.0
    versionCode: 2
    commit: v1.0.0
    subdir: app
    gradle:
      - yes

AutoUpdateMode: None
UpdateCheckMode: Tags
CurrentVersion: 1.0.0
CurrentVersionCode: 2
"""

CHANGELOG = """# Changelog

All notable changes to NexaNote are documented in this file.

---

## Unreleased — Sync reliability & diagnostics

### New

- A thing.

---

## v1.0.0 — File-based storage

- The first thing.
"""


# --- validation ------------------------------------------------------------

@pytest.mark.parametrize("good", ["1.0.0", "0.1.0", "12.34.56"])
def test_parse_version_name_accepts_semver(good):
    assert bump.parse_version_name(good) == good


@pytest.mark.parametrize("bad", ["1.0", "1.0.0.0", "v1.0.0", "1.0.x", "1.0.0-rc1"])
def test_parse_version_name_rejects_non_semver(bad):
    with pytest.raises(ValueError):
        bump.parse_version_name(bad)


def test_parse_version_code_accepts_positive_int():
    assert bump.parse_version_code("3") == 3
    assert bump.parse_version_code(3) == 3


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5"])
def test_parse_version_code_rejects_bad(bad):
    with pytest.raises(ValueError):
        bump.parse_version_code(bad)


# --- pubspec ---------------------------------------------------------------

def test_bump_pubspec_rewrites_version():
    out = bump.bump_pubspec(PUBSPEC, "1.0.2", 3)
    assert "version: 1.0.2+3\n" in out
    assert "version: 1.0.0+2" not in out
    # Nothing else should move.
    assert "name: nexanote" in out
    assert "sdk: '>=3.0.0 <4.0.0'" in out


def test_read_pubspec_version_code():
    assert bump.read_pubspec_version_code(PUBSPEC) == 2
    assert bump.read_pubspec_version_code("name: x\n") is None


def test_bump_pubspec_without_version_line_raises():
    with pytest.raises(ValueError):
        bump.bump_pubspec("name: nexanote\n", "1.0.2", 3)


# --- F-Droid metadata ------------------------------------------------------

def test_metadata_appends_build_and_updates_current():
    out = bump.bump_fdroid_metadata(METADATA, "1.0.2", 3)
    assert "CurrentVersion: 1.0.2" in out
    assert "CurrentVersionCode: 3" in out
    # Old build entry is preserved (F-Droid keeps build history)...
    assert "versionName: 1.0.0" in out
    assert "versionName: 1.0.2" in out
    # ...and the new entry has a matching versionCode and vX.Y.Z commit.
    assert "versionCode: 3" in out
    assert "commit: v1.0.2" in out


def test_metadata_preserves_gradle_yes_marker():
    # PyYAML would turn the bare `yes` into `true`; the line-based bump must not.
    out = bump.bump_fdroid_metadata(METADATA, "1.0.2", 3)
    assert out.count("      - yes") == 2
    assert "- true" not in out


def test_metadata_new_entry_is_wellformed_yaml():
    import yaml

    out = bump.bump_fdroid_metadata(METADATA, "1.0.2", 3)
    data = yaml.safe_load(out)
    codes = [b["versionCode"] for b in data["Builds"]]
    assert codes == [2, 3]
    newest = data["Builds"][-1]
    assert newest["versionName"] == "1.0.2"
    assert newest["commit"] == "v1.0.2"
    assert newest["subdir"] == "app"
    assert data["CurrentVersion"] == "1.0.2"
    assert data["CurrentVersionCode"] == 3


def test_metadata_bump_is_idempotent():
    once = bump.bump_fdroid_metadata(METADATA, "1.0.2", 3)
    twice = bump.bump_fdroid_metadata(once, "1.0.2", 3)
    # Re-running must not add a duplicate build entry.
    assert once == twice
    assert twice.count("versionCode: 3") == 1


# --- changelog -------------------------------------------------------------

def test_changelog_rolls_unreleased_into_version():
    out = bump.bump_changelog(CHANGELOG, "1.0.2")
    # The old Unreleased content becomes the v1.0.2 release, keeping its subtitle.
    assert "## v1.0.2 — Sync reliability & diagnostics" in out
    # A fresh Unreleased placeholder is left at the top of the entries.
    assert "## Unreleased\n" in out
    assert "_Nothing yet._" in out
    # The release body (the "A thing." note) is retained under v1.0.2.
    assert "- A thing." in out
    # Order: new Unreleased placeholder comes before the new version heading.
    assert out.index("## Unreleased") < out.index("## v1.0.2")
    assert out.index("## v1.0.2") < out.index("## v1.0.0")


def test_changelog_without_unreleased_is_unchanged():
    text = "# Changelog\n\n## v1.0.0\n\n- thing\n"
    assert bump.bump_changelog(text, "1.0.2") == text


# --- end-to-end (real files, temp copy) ------------------------------------

def _stage_repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "metadata").mkdir()
    shutil.copy(ROOT / "app" / "pubspec.yaml", tmp_path / "app" / "pubspec.yaml")
    shutil.copy(
        ROOT / "metadata" / "com.nexanote.app.yml",
        tmp_path / "metadata" / "com.nexanote.app.yml",
    )
    shutil.copy(ROOT / "CHANGELOG.md", tmp_path / "CHANGELOG.md")
    return tmp_path


def test_main_updates_all_files(tmp_path):
    repo = _stage_repo(tmp_path)
    rc = bump.main(["1.9.9", "99", "--root", str(repo)])
    assert rc == 0

    pubspec = (repo / "app" / "pubspec.yaml").read_text()
    assert "version: 1.9.9+99" in pubspec

    meta = (repo / "metadata" / "com.nexanote.app.yml").read_text()
    assert "CurrentVersion: 1.9.9" in meta
    assert "CurrentVersionCode: 99" in meta
    assert "commit: v1.9.9" in meta

    changelog = (repo / "CHANGELOG.md").read_text()
    assert "## v1.9.9" in changelog


def test_main_rejects_version_code_regression(tmp_path):
    repo = _stage_repo(tmp_path)
    # The live pubspec is well above 1; a versionCode of 1 must be refused.
    rc = bump.main(["1.9.9", "1", "--root", str(repo)])
    assert rc == 2
    # Pubspec untouched on a rejected bump.
    assert "1.9.9+1" not in (repo / "app" / "pubspec.yaml").read_text()


def test_main_force_allows_regression(tmp_path):
    repo = _stage_repo(tmp_path)
    rc = bump.main(["1.9.9", "1", "--force", "--root", str(repo)])
    assert rc == 0
    assert "version: 1.9.9+1" in (repo / "app" / "pubspec.yaml").read_text()


def test_main_no_changelog_flag(tmp_path):
    repo = _stage_repo(tmp_path)
    before = (repo / "CHANGELOG.md").read_text()
    rc = bump.main(["1.9.9", "99", "--no-changelog", "--root", str(repo)])
    assert rc == 0
    assert (repo / "CHANGELOG.md").read_text() == before


def test_main_does_not_create_git_tag_by_default(tmp_path):
    """The script must never tag git unless --tag is passed."""
    repo = _stage_repo(tmp_path)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    rc = bump.main(["1.9.9", "99", "--root", str(repo)])
    assert rc == 0
    tags = subprocess.run(
        ["git", "-C", str(repo), "tag"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert tags == ""


def test_script_runs_as_subprocess(tmp_path):
    """Smoke-test the CLI entry point itself."""
    repo = _stage_repo(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "2.0.0", "100", "--root", str(repo)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "version: 2.0.0+100" in (repo / "app" / "pubspec.yaml").read_text()
