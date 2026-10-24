# spaceband

A 3D map of music artists built from ListenBrainz similarity data. `web/` is the
whole app (plain ES modules + three.js, no bundler); `pipeline/` builds
`web/data/map.json`; `android/` wraps `web/` with Capacitor.

## Finishing a change

When work that touches `web/` is done, check whether an Android device is
connected:

```bash
"$LOCALAPPDATA/Android/Sdk/platform-tools/adb.exe" devices
```

(`adb` is not on PATH on this machine; use that full path.)

If a device is listed (and authorised), always build and install the app on it
before reporting the work finished:

```bash
npx cap sync android
cd android && ANDROID_HOME="C:/Users/shale/AppData/Local/Android/Sdk"   JAVA_HOME="C:/Users/shale/.jdks/temurin-21" ./gradlew assembleRelease
"$LOCALAPPDATA/Android/Sdk/platform-tools/adb.exe" install -r   android/app/build/outputs/apk/release/app-release.apk
```

Both env vars are needed: there is no `local.properties`, and the default JDK on
this machine is 17 while the build wants 21. Copy the result over
`apk/spaceband.apk` so the repo's copy matches.

Mention in the summary that it was installed, or — if no device is connected —
that it wasn't. An update only installs over the existing app when it is signed
with the same key (`android/app/keystore.properties` + `spaceband-release.jks`);
if `adb install` fails with a signature mismatch, say so rather than
uninstalling the user's app.

## Notes

- Bump the `?v=N` query on `style.css` / `app.js` in `web/index.html` whenever
  either changes, so phones and the WebView don't serve a stale copy.
- Bump `versionCode` in `android/app/build.gradle` for every release build.
