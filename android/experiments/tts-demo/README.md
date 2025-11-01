# Sherpa-ONNX TTS Demo

This module is a minimal Android app that synthesizes and plays a test paragraph
locally on-device using the sherpa-onnx offline TTS engine. The project lives
under `android/experiments/tts-demo` so it can evolve independently of the main
Ringdown client.

## Populate `android/third_party`

The demo relies on artifacts that are **not** checked into the repo. Fetch them
once per machine:

1. Create the third-party layout:

   ```bash
   mkdir -p android/third_party/models
   ```

2. Download the sherpa-onnx Android AAR (v1.12.15 at the time of writing):

   ```bash
   curl -L -o android/third_party/sherpa-onnx-1.12.15.aar \
     https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.12.15/sherpa-onnx-1.12.15.aar
   ```

3. Download and extract the Piper “Amy” model bundle:

   ```bash
   curl -L -o android/third_party/models/vits-piper-en_US-amy-low.tar.bz2 \
     https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-en_US-amy-low.tar.bz2

   tar -xjf android/third_party/models/vits-piper-en_US-amy-low.tar.bz2 \
     -C android/third_party/models

   rm android/third_party/models/vits-piper-en_US-amy-low.tar.bz2
   ```

   The TTS engine reads the ONNX model directly from the APK assets, while the
   app copies `espeak-ng-data/` to internal storage on first launch.

## Build and deploy

From the repo root:

```powershell
$env:JAVA_HOME = 'D:/Users/Dan/GoogleDrivePersonal/code/ringdown/android/.jdk'
Set-Location android
./gradlew.bat :experiments:tts-demo:assembleDebug        # build
./gradlew.bat :experiments:tts-demo:installDebug         # deploy to attached device
```

On macOS/Linux, replace the PowerShell commands with:

```bash
export JAVA_HOME="$PWD/android/.jdk"
cd android
./gradlew :experiments:tts-demo:assembleDebug
./gradlew :experiments:tts-demo:installDebug
```

After installation, launch the app (e.g., `adb shell am start -n
com.ringdown.ttsdemo/.MainActivity`). The activity will log progress under the
`SherpaTtsDemo` tag, synthesize the demo paragraph, and play it through the
handset speaker.

## Notes

- `android/third_party/` remains ignored by Git; each developer must download
  the assets locally before building.
- The module is self-contained: it does not affect the main Ringdown Android
  client unless explicitly added as a dependency.
