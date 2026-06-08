# Android signing & update behaviour

NexaNote ships its Android client as an APK attached to each GitHub Release
(`NexaNote-Android.apk`). For an update to install **over** an existing install
— preserving app data, no uninstall/reinstall — Android requires that the new
APK is signed with the **same key** as the installed one. This document explains
how that is set up and the one caveat to be aware of.

## How signing works here

`app/android/app/build.gradle.kts` chooses a release signing config at build
time:

- **If `app/android/key.properties` exists** (CI creates it from repository
  secrets — it is git-ignored and never committed), the release build is signed
  with a stable **upload key**. Every GitHub Release APK is then signed with the
  same key, so each one installs as an update over the previous Release APK.
- **If it is absent** (a contributor's local checkout, or an F-Droid
  reproducible build), the build falls back to the **debug key** so
  `flutter build apk --release` and `flutter run --release` still work without
  any secrets, and F-Droid can apply its own signature.

`applicationId` stays `com.nexanote.app` and `versionCode` increases every
release (driven by `app/pubspec.yaml`'s `version: X.Y.Z+versionCode`), which is
what Android uses to recognise an APK as a newer build of the same app.

## CI setup (maintainers)

The release workflow (`.github/workflows/android-release.yml`) writes
`key.properties` from four repository secrets, then builds:

| Secret | Meaning |
| --- | --- |
| `ANDROID_KEYSTORE_BASE64` | base64 of the `.jks`/keystore file |
| `ANDROID_KEYSTORE_PASSWORD` | keystore (store) password |
| `ANDROID_KEY_ALIAS` | key alias inside the keystore |
| `ANDROID_KEY_PASSWORD` | password for that key |

Generate an upload keystore once and keep it safe (losing it means future
releases can no longer update existing installs):

```bash
keytool -genkey -v -keystore nexanote-upload.jks \
  -keyalg RSA -keysize 2048 -validity 10000 -alias upload
# then:
base64 -w0 nexanote-upload.jks   # paste into ANDROID_KEYSTORE_BASE64
```

Add the four secrets under **Settings → Secrets and variables → Actions**. If
they are not set, the workflow logs a warning and produces a debug-signed APK
(it still builds; it just won't reliably update over a prior release).

## Caveat: signing mismatches

- **Local debug builds cannot update Release builds.** An APK you build locally
  with `flutter build apk` is debug-signed with *your machine's* debug key,
  which differs from the CI release key. Installing it over a GitHub Release
  install (or vice-versa) fails with "App not installed" / signature mismatch.
  Uninstall first if you need to switch between the two.
- **F-Droid builds are signed by F-Droid**, not by this key. F-Droid installs
  can only be updated from F-Droid, and GitHub Release installs can only be
  updated from GitHub Releases. Pick one source per device.
- **The historical problem this fixes:** before a stable key was configured,
  release APKs were signed with an *ephemeral* debug key generated fresh on each
  CI runner, so two GitHub Release APKs had different signatures and could not
  update each other. With the upload key in place, consecutive GitHub Release
  APKs are consistently installable over one another.
