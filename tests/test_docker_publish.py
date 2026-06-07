"""Static checks on the Docker publish workflow's release versioning.

These guard that one `vX.Y.Z` tag drives the Docker image version exactly like
the APK and F-Droid metadata, without breaking the existing publish behavior
(the `latest`, `${sha}`, and `-backend` tags that current docs rely on).
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "docker-publish.yml"


def _load():
    data = yaml.safe_load(WORKFLOW.read_text())
    assert data is not None, "docker-publish workflow is empty or invalid YAML"
    return data


def _workflow_on(data):
    # PyYAML parses the bare `on:` key as boolean True under YAML 1.1.
    return data["on"] if "on" in data else data[True]


def test_workflow_is_valid_and_has_build_job():
    assert "build" in _load()["jobs"]


def test_triggers_on_version_tags():
    on = _workflow_on(_load())
    assert "v*" in on["push"]["tags"], "vX.Y.Z tags must trigger a publish"


def test_publishes_clean_version_tag_from_vtag():
    text = WORKFLOW.read_text()
    # The leading `v` is stripped so the image tag is X.Y.Z, not vX.Y.Z.
    assert "${GITHUB_REF_NAME#v}" in text
    assert "thezupzup/nexanote:${version}" in text


def test_still_publishes_latest():
    assert "thezupzup/nexanote:latest" in WORKFLOW.read_text()


def test_version_tag_is_guarded_by_tag_ref_type():
    # A branch push (e.g. main, where ref_name is "main") must not be treated as
    # a version — the version tag is only added when GITHUB_REF_TYPE == tag.
    text = WORKFLOW.read_text()
    assert 'GITHUB_REF_TYPE' in text
    assert '= "tag" ]' in text


def test_does_not_break_existing_tags():
    # The existing contract — short + `-backend` names, latest + per-commit sha —
    # must be preserved so no documented `docker pull` command breaks.
    text = WORKFLOW.read_text()
    assert "thezupzup/nexanote-backend:latest" in text
    assert "${GITHUB_SHA}" in text
    assert "thezupzup/nexanote-backend:${GITHUB_SHA}" in text


def test_build_step_consumes_computed_tags():
    text = WORKFLOW.read_text()
    assert "steps.meta.outputs.tags" in text
    assert "docker/build-push-action" in text
    # Multi-arch publishing must remain intact.
    assert "linux/amd64,linux/arm64" in text
