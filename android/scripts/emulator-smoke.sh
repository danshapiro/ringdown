#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: emulator-smoke.sh [--device <serial>]

Assembles the debug APK, installs it on a running emulator, and launches the main
activity to verify the scaffold boots. If --device is not supplied, the script
uses $ANDROID_SERIAL or falls back to the first connected emulator.
EOF
}

DEVICE_SERIAL="${ANDROID_SERIAL:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --device)
      [[ $# -ge 2 ]] || { echo "Error: missing value for --device" >&2; exit 1; }
      DEVICE_SERIAL="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$ANDROID_ROOT/app"
APP_ID="${APP_ID_OVERRIDE:-com.ringdown}"

[[ -d "$PROJECT_ROOT" ]] || { echo "Error: Android project not initialized. Run android/scripts/init-project.sh first." >&2; exit 1; }

"$SCRIPT_DIR/gradle.sh" ./gradlew :app:assembleDebug

APK_PATH="$PROJECT_ROOT/app/build/outputs/apk/debug/app-debug.apk"
[[ -f "$APK_PATH" ]] || { echo "Error: APK not found at $APK_PATH" >&2; exit 1; }

ADB_BIN="${ANDROID_SDK_ROOT:-$ANDROID_ROOT/.android-sdk}/platform-tools/adb"
[[ -x "$ADB_BIN" ]] || { echo "Error: adb not found. Ensure the SDK platform-tools are installed." >&2; exit 1; }

if [[ -z "$DEVICE_SERIAL" ]]; then
  DEVICE_SERIAL=$("$ADB_BIN" devices | awk 'NR>1 && $2=="device" {print $1; exit}')
fi

[[ -n "$DEVICE_SERIAL" ]] || { echo "Error: no emulator device detected. Start an Android emulator first." >&2; exit 1; }

echo "Installing build to emulator $DEVICE_SERIAL..."
"$ADB_BIN" -s "$DEVICE_SERIAL" install -r "$APK_PATH"

echo "Launching $APP_ID/.MainActivity..."
"$ADB_BIN" -s "$DEVICE_SERIAL" shell am start -n "$APP_ID/.MainActivity"

echo "Emulator smoke test complete."
