# NexaNote (Flutter app)

The Flutter client for NexaNote, an open-source, privacy-friendly note-taking app.

## Branding

Installed builds are branded as **NexaNote**:

- App package: `com.nexanote.app`
- Android launcher label: `NexaNote` (`android:label` in `android/app/src/main/AndroidManifest.xml`)
- Launcher icon: `android/app/src/main/res/mipmap-*/ic_launcher.png`, plus an
  adaptive icon (`mipmap-anydpi-v26/ic_launcher.xml` with
  `ic_launcher_foreground.png` and the `ic_launcher_background` color).
- Linux window title: `NexaNote` (`linux/runner/my_application.cc`)

The APK installs and appears in the app drawer as **NexaNote**.

## Build the Android APK

```sh
flutter pub get
flutter build apk --release
```

The release APK is written to `build/app/outputs/flutter-apk/app-release.apk`.
