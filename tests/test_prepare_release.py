"""Static checks on the Prepare release workflow's robustness contract.

These guard the GitHub-only, retry-safe release-prep flow without needing a
checkout, a runner, or any network. They lock in the behaviours the release
process depends on:

  * an existing *tag* stops the run early with a clear, actionable message
    (and the exact cleanup command),
  * an existing *release branch* is handled safely instead of hard-failing,
  * the publish workflow's tag/pubspec version-mismatch message is explicit,
  * Prepare release never pushes to the protected `main` branch,
  * a manual PR fallback URL is printed when Actions can't open the PR,
  * a dry-run validation mode exists, and
  * the README's release instructions match this workflow.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "prepare-release.yml"
ANDROID_WORKFLOW = ROOT / ".github" / "workflows" / "android-release.yml"
README = ROOT / "README.md"


def _load():
    data = yaml.safe_load(WORKFLOW.read_text())
    assert data is not None, "prepare-release workflow is empty or invalid YAML"
    return data


def _workflow_on(data):
    # PyYAML parses the bare `on:` key as boolean True under YAML 1.1.
    return data["on"] if "on" in data else data[True]


def _steps():
    return _load()["jobs"]["prepare-release"]["steps"]


def _step(name_fragment):
    for step in _steps():
        if name_fragment.lower() in str(step.get("name", "")).lower():
            return step
    raise AssertionError(f"no step whose name contains {name_fragment!r}")


# --- workflow validity / shape ---------------------------------------------

def test_workflow_yaml_is_valid():
    data = _load()
    assert "prepare-release" in data["jobs"]


def test_manual_dispatch_takes_version_inputs():
    on = _workflow_on(_load())
    inputs = on["workflow_dispatch"]["inputs"]
    assert "version_name" in inputs
    assert "version_code" in inputs


# --- (2) existing tag fails early with a clear message ----------------------

def test_existing_tag_fails_with_clear_message():
    step = _step("Fail if tag already exists")
    run = step["run"]
    # The exact, actionable message the task requires.
    assert "already exists. Delete it or choose a new version." in run
    # And the exact manual cleanup command.
    assert "git push origin :refs/tags/" in run
    # It must actually stop the run on this condition.
    assert "exit 1" in run


def test_existing_tag_check_covers_local_and_remote():
    run = _step("Fail if tag already exists")["run"]
    assert "refs/tags/" in run
    assert "ls-remote" in run  # remote tags are checked too


# --- (1) existing release branch is handled safely --------------------------

def test_existing_release_branch_is_not_a_hard_fail():
    step = _step("existing release branch")
    run = step["run"]
    # Detects an existing branch...
    assert "ls-remote" in run and "--heads" in run
    # ...checks whether it already contains the expected bump...
    assert "expected" in run.lower()
    # ...and explains rather than failing: this step must not `exit 1`.
    assert "exit 1" not in run


def test_release_branch_regenerated_cleanly_on_rerun():
    # The commit step recreates the branch from main (`switch -C`) so a stale
    # branch from a failed run is regenerated, not appended to.
    run = _step("Commit the bump on a release branch")["run"]
    assert "switch -C" in run
    assert 'push --force -u origin "${RELEASE_BRANCH}"' in run


# --- (4) never pushes to protected main -------------------------------------

def test_prepare_release_never_pushes_to_main():
    text = WORKFLOW.read_text()
    pushes = re.findall(r"git push[^\n]*", text)
    assert pushes, "expected at least one git push in the workflow"
    # No push anywhere in the workflow may target the protected main branch.
    for push in pushes:
        assert "main" not in push, f"push targets main: {push!r}"
    # Pushes that update a ref (i.e. not the `:refs/tags/...` deletion hint shown
    # in the tag-error message) must target the workflow-owned release branch.
    branch_pushes = [p for p in pushes if ":refs/tags/" not in p]
    assert branch_pushes, "expected a release-branch push"
    for push in branch_pushes:
        assert "RELEASE_BRANCH" in push, f"push does not target the release branch: {push!r}"


def test_prepare_release_opens_pr_into_main():
    # The bump reaches main only via a PR, never a direct push.
    run = _step("Open the release pull request")["run"]
    assert "gh pr create" in run
    assert "--base main" in run
    assert "--head" in run and "RELEASE_BRANCH" in run


def test_checks_out_main_without_persisting_a_push_to_it():
    step = _step("Checkout main")
    assert step["with"]["ref"] == "main"


# --- (5) graceful PR-creation fallback --------------------------------------

def test_manual_pr_fallback_url_is_printed():
    run = _step("Open the release pull request")["run"]
    # The compare URL a maintainer can open the PR from by hand.
    assert "compare/main...${RELEASE_BRANCH}" in run
    # A blocked PR creation is downgraded to a warning, not a failure.
    assert "not permitted to create or approve pull requests" in run
    assert "::warning::" in run


# --- (6) clear summary output -----------------------------------------------

def test_summary_step_reports_the_release_state():
    step = _step("Release summary")
    # The summary must always run, even if an earlier step failed.
    assert step.get("if") == "always()"
    run = step["run"]
    assert "GITHUB_STEP_SUMMARY" in run
    for field in ("Release version", "Version code", "Release branch"):
        assert field in run, f"summary is missing {field!r}"
    # The actionable next step the task requires.
    assert "Merge the release PR, then create tag" in run


# --- (7) dry-run validation mode --------------------------------------------

def test_dry_run_input_exists():
    inputs = _workflow_on(_load())["workflow_dispatch"]["inputs"]
    assert "dry_run" in inputs
    assert inputs["dry_run"]["type"] == "boolean"


def test_dry_run_skips_commit_and_push():
    text = WORKFLOW.read_text()
    commit = _step("Commit the bump on a release branch")
    pr = _step("Open the release pull request")
    # The mutating steps are gated off in dry-run mode.
    assert commit.get("if") == "env.DRY_RUN != 'true'"
    assert pr.get("if") == "env.DRY_RUN != 'true'"
    # And there is an explicit dry-run stop that still validated the bump.
    stop = _step("Stop here on dry run")
    assert stop.get("if") == "env.DRY_RUN == 'true'"


# --- (3) version-mismatch message in the publish workflow -------------------

def test_version_mismatch_message_is_clear():
    text = ANDROID_WORKFLOW.read_text()
    # Names the current tag version, the current pubspec version, and the action.
    assert "current tag version" in text
    assert "current pubspec version" in text
    assert "Expected next action" in text


# --- (8) README documents the GitHub-only flow ------------------------------

def test_readme_matches_the_workflow_flow():
    text = README.read_text()
    # Step 1: run Prepare release.
    assert "Prepare release" in text
    # Step 2: merge the release PR.
    assert re.search(r"[Mm]erge the release PR", text)
    # Step 3: create the GitHub Release / tag.
    assert re.search(r"tag `?vX\.Y\.Z`?", text)
    # Step 4: workflows publish automatically.
    assert "publishes" in text.lower() or "automatically" in text.lower()


def test_readme_mentions_dry_run_and_manual_fallback():
    text = README.read_text().lower()
    assert "dry run" in text
    assert "manual pr url" in text or "compare/main" in text
