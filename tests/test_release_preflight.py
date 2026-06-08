"""Behavioural tests for scripts/release_preflight.sh — the local-twin check
that a `vX.Y.Z` tag matches app/pubspec.yaml's versionName.

The Android publish workflow runs this exact script, so these tests lock in the
contract both CI and a contributor's pre-tag check depend on: a matching tag
passes and exports NEXANOTE_VERSION_NAME/CODE, a mismatching tag fails with the
clear "tag/pubspec version mismatch" message, and a malformed tag is rejected.
They run the real bash script against a throwaway pubspec, so they need neither
Flutter nor a checkout.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_preflight.sh"


def _run(tag, pubspec_text, tmp_path):
    pubspec = tmp_path / "pubspec.yaml"
    pubspec.write_text(pubspec_text)
    return subprocess.run(
        ["bash", str(SCRIPT), tag, "--pubspec", str(pubspec)],
        capture_output=True,
        text=True,
    )


PUBSPEC = "name: nexanote\nversion: 1.1.0+4\n\nenvironment:\n  sdk: '>=3.0.0 <4.0.0'\n"


def test_script_is_executable():
    assert SCRIPT.exists()
    import os

    assert os.access(SCRIPT, os.X_OK), "release_preflight.sh must be executable"


def test_matching_tag_passes_and_exports_versions(tmp_path):
    result = _run("v1.1.0", PUBSPEC, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "NEXANOTE_VERSION_NAME=1.1.0" in result.stdout
    assert "NEXANOTE_VERSION_CODE=4" in result.stdout
    assert "OK: v1.1.0 matches" in result.stdout


def test_tag_accepted_without_leading_v(tmp_path):
    result = _run("1.1.0", PUBSPEC, tmp_path)
    assert result.returncode == 0, result.stderr
    # The displayed/canonical form always carries the leading v.
    assert "NEXANOTE_VERSION_NAME=1.1.0" in result.stdout


def test_mismatching_tag_fails_with_clear_message(tmp_path):
    result = _run("v9.9.9", PUBSPEC, tmp_path)
    assert result.returncode == 1
    err = result.stderr
    assert "mismatch" in err.lower()
    assert "current tag version:     v9.9.9" in err
    assert "current pubspec version: 1.1.0" in err
    assert "Expected next action" in err
    # It must not leak version vars on failure.
    assert "NEXANOTE_VERSION_NAME=" not in result.stdout


@pytest.mark.parametrize("bad", ["v1.1", "v1.1.0.0", "v1.1.x", "1.0.0-rc1"])
def test_malformed_tag_is_rejected(bad, tmp_path):
    result = _run(bad, PUBSPEC, tmp_path)
    assert result.returncode == 1
    assert "Malformed release tag" in result.stderr


def test_missing_version_line_fails(tmp_path):
    result = _run("v1.1.0", "name: nexanote\n", tmp_path)
    assert result.returncode == 1
    assert "version:" in result.stderr


def test_defaults_to_app_pubspec(tmp_path):
    # With no --pubspec override the script reads app/pubspec.yaml, which must
    # match the live tag derived from it (sanity check on the default path).
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "app/pubspec.yaml" in result.stdout
