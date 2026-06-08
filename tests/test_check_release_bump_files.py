"""Tests for scripts/check_release_bump_files.sh and its CI wiring.

The guard keeps a release-bump PR a clean, version-only diff: it allows exactly
the files the Prepare release workflow / scripts/bump_version.py touch
(app/pubspec.yaml, the F-Droid metadata, CHANGELOG.md — plus app/pubspec.lock
and docs/release-notes/**) and fails, naming the offenders, on anything else.
The CI job that runs it is scoped to release/* PRs so it never blocks normal
work. These run the real bash script and parse the workflow, needing no network.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_release_bump_files.sh"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "python-app.yml"

import subprocess


def _run(paths):
    return subprocess.run(
        ["bash", str(SCRIPT), *paths],
        capture_output=True,
        text=True,
    )


def test_script_is_executable():
    import os

    assert SCRIPT.exists()
    assert os.access(SCRIPT, os.X_OK)


def test_allows_the_exact_bump_fileset():
    result = _run(
        ["app/pubspec.yaml", "metadata/com.nexanote.app.yml", "CHANGELOG.md"]
    )
    assert result.returncode == 0, result.stderr
    assert "OK:" in result.stdout


def test_allows_lockfile_and_release_notes():
    result = _run(["app/pubspec.lock", "docs/release-notes/v1.2.0.md"])
    assert result.returncode == 0, result.stderr


def test_rejects_unrelated_change_and_names_it():
    result = _run(["app/pubspec.yaml", "main.py"])
    assert result.returncode == 1
    # The offender is named in the offenders list (above the allowed-files help).
    offenders_section = result.stderr.split("A release bump should be")[0]
    assert "- main.py" in offenders_section
    # The compliant file must not be reported as an offender.
    assert "- app/pubspec.yaml" not in offenders_section


def test_rejects_app_source_change():
    result = _run(["app/lib/screens/settings_screen.dart"])
    assert result.returncode == 1
    assert "settings_screen.dart" in result.stderr


def test_empty_input_is_a_no_op():
    result = _run([])
    assert result.returncode == 0


def test_reads_paths_from_stdin():
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        input="app/pubspec.yaml\nbackend.py\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "backend.py" in result.stderr


# --- CI wiring --------------------------------------------------------------

def _ci():
    data = yaml.safe_load(CI_WORKFLOW.read_text())
    assert data is not None
    return data


def test_ci_has_release_bump_guard_job():
    jobs = _ci()["jobs"]
    assert "release-bump-guard" in jobs


def test_guard_job_is_scoped_to_release_branches():
    job = _ci()["jobs"]["release-bump-guard"]
    # Only runs for the release/* PRs the prepare workflow opens, so it never
    # blocks normal PRs or pushes to main.
    assert "startsWith(github.head_ref, 'release/')" in job["if"]


def test_guard_job_runs_the_check_script():
    text = CI_WORKFLOW.read_text()
    assert "scripts/check_release_bump_files.sh" in text
    assert "git diff --name-only" in text
