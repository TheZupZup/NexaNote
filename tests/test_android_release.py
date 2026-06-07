"""Checks that the Android release pipeline stays Obtainium- and F-Droid-friendly.

These are static checks over the workflow, the Flutter pubspec/Gradle config,
the Settings/About screen, and the F-Droid metadata. They do not need Flutter
installed — they guard the contract that Obtainium relies on (a stable APK asset
name and tag/release wiring) and the F-Droid alignment rules, so a future edit
that breaks them fails CI loudly.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "android-release.yml"
PUBSPEC = ROOT / "app" / "pubspec.yaml"
GRADLE = ROOT / "app" / "android" / "app" / "build.gradle.kts"
SETTINGS = ROOT / "app" / "lib" / "screens" / "settings_screen.dart"
METADATA = ROOT / "metadata" / "com.nexanote.app.yml"

# The stable asset name Obtainium tracks across releases.
APK_ASSET = "NexaNote-Android.apk"
# Where `flutter build apk --release` writes its output.
APK_BUILD_PATH = "app/build/app/outputs/flutter-apk/app-release.apk"


def _load_workflow():
    data = yaml.safe_load(WORKFLOW.read_text())
    assert data is not None, "android-release workflow is empty or invalid YAML"
    return data


def _workflow_on(data):
    # PyYAML parses the bare key `on:` as the boolean True (YAML 1.1), so accept
    # either spelling.
    return data["on"] if "on" in data else data[True]


def _release_step(data):
    steps = data["jobs"]["build-android"]["steps"]
    for step in steps:
        if str(step.get("uses", "")).startswith("softprops/action-gh-release"):
            return step
    raise AssertionError("no softprops/action-gh-release step found")


# --- workflow validity / triggers -----------------------------------------

def test_workflow_yaml_is_valid():
    data = _load_workflow()
    assert "build-android" in data["jobs"]


def test_tag_push_triggers_build():
    on = _workflow_on(_load_workflow())
    assert "v*" in on["push"]["tags"], "vX.Y.Z tags must trigger the build"


# --- predictable APK asset for Obtainium -----------------------------------

def test_release_attaches_stable_named_apk():
    step = _release_step(_load_workflow())
    files = step["with"]["files"]
    assert APK_ASSET in files, (
        "the GitHub Release must attach the stable-named "
        f"{APK_ASSET} so Obtainium can track updates"
    )


def test_release_title_is_nexanote_version():
    step = _release_step(_load_workflow())
    # github.ref_name on a tag is `vX.Y.Z`, so this renders "NexaNote vX.Y.Z".
    assert step["with"]["name"] == "NexaNote ${{ github.ref_name }}"


def test_workflow_fails_when_apk_missing():
    text = WORKFLOW.read_text()
    step = _release_step(_load_workflow())
    assert step["with"]["fail_on_unmatched_files"] is True
    # The artifact upload must also error on a missing APK...
    assert "if-no-files-found: error" in text
    # ...and the prepare step explicitly verifies the build produced an APK.
    assert APK_BUILD_PATH in text
    assert "::error::" in text and "exit 1" in text


def test_workflow_checks_tag_matches_pubspec_version():
    text = WORKFLOW.read_text()
    assert "Verify tag matches pubspec version" in text
    assert "pubspec" in text and "GITHUB_REF_NAME" in text


# --- versioning: pubspec is the source of truth ----------------------------

def _pubspec_version():
    m = re.search(r"^version:\s*(\d+\.\d+\.\d+)\+(\d+)\s*$",
                  PUBSPEC.read_text(), re.MULTILINE)
    assert m, "pubspec.yaml must declare `version: X.Y.Z+BUILD`"
    return m.group(1), m.group(2)


def test_pubspec_version_is_wellformed():
    version_name, version_code = _pubspec_version()
    assert version_name.count(".") == 2
    assert int(version_code) >= 1


def test_android_version_follows_flutter_pubspec():
    text = GRADLE.read_text()
    # Android must inherit versionName/versionCode from Flutter (pubspec), not
    # hardcode them — keeps the tag, the APK, and the About screen in lockstep.
    assert "versionName = flutter.versionName" in text
    assert "versionCode = flutter.versionCode" in text


# --- version shown in the app comes from package metadata ------------------

def test_about_version_comes_from_package_metadata():
    text = SETTINGS.read_text()
    assert "package_info_plus" in text
    assert "PackageInfo.fromPlatform(" in text, (
        "About must read the installed version from package metadata"
    )
    # No hardcoded version string left behind in the About card.
    assert "v0.1.0" not in text


def test_about_has_no_placeholder_repo_url():
    text = SETTINGS.read_text()
    assert "YOUR_USER" not in text
    assert "github.com/TheZupZup/NexaNote" in text


def test_package_info_plus_declared_in_pubspec():
    assert re.search(r"^\s*package_info_plus:\s*\^?\d",
                     PUBSPEC.read_text(), re.MULTILINE), \
        "package_info_plus must be a declared dependency"


# --- F-Droid alignment ------------------------------------------------------

def test_fdroid_metadata_is_mpl2_and_play_free():
    meta = yaml.safe_load(METADATA.read_text())
    assert meta["License"] == "MPL-2.0"
    # No proprietary update mechanism / Google Play dependency.
    assert meta.get("AutoUpdateMode") == "None"
    assert meta.get("UpdateCheckMode") == "Tags"
    blob = METADATA.read_text().lower()
    assert "play-services" not in blob
    assert "gms" not in blob


def test_no_google_play_dependency_in_app():
    pub = PUBSPEC.read_text().lower()
    for forbidden in ("play_services", "play-services", "firebase", "gms"):
        assert forbidden not in pub, f"{forbidden} must not be an app dependency"


def test_internet_permission_is_documented():
    # The single declared permission must be explained for F-Droid reviewers.
    manifest = (ROOT / "app" / "android" / "app" / "src" / "main"
                / "AndroidManifest.xml").read_text()
    assert "android.permission.INTERNET" in manifest
    assert "INTERNET" in METADATA.read_text()
