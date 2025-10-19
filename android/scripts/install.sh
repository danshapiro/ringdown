#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install.sh --device <serial> [--variant <gradleTask>]

Builds the specified variant (default: :app:assembleDebug) and installs the resulting
APK on the provided device serial via adb.
EOF
}

DEVICE_SERIAL=""
VARIANT=":app:assembleDebug"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --device)
      [[ $# -ge 2 ]] || { echo "Error: missing value for --device" >&2; exit 1; }
      DEVICE_SERIAL="$2"
      shift 2
      ;;
    --variant)
      [[ $# -ge 2 ]] || { echo "Error: missing value for --variant" >&2; exit 1; }
      VARIANT="$2"
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

[[ -n "$DEVICE_SERIAL" ]] || { echo "Error: --device is required" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$ANDROID_ROOT/app"
APP_ID="${APP_ID_OVERRIDE:-com.ringdown}"

[[ -d "$PROJECT_ROOT" ]] || { echo "Error: Android project not initialized. Run android/scripts/init-project.sh first." >&2; exit 1; }

"$SCRIPT_DIR/gradle.sh" ./gradlew "$VARIANT"

APK_PATH="$PROJECT_ROOT/app/build/outputs/apk/debug/app-debug.apk"
[[ -f "$APK_PATH" ]] || { echo "Error: APK not found at $APK_PATH" >&2; exit 1; }

ADB_BIN="${ANDROID_SDK_ROOT:-$ANDROID_ROOT/.android-sdk}/platform-tools/adb"
[[ -x "$ADB_BIN" ]] || { echo "Error: adb not found. Ensure the SDK platform-tools are installed." >&2; exit 1; }

echo "Installing build to device $DEVICE_SERIAL..."
"$ADB_BIN" -s "$DEVICE_SERIAL" install -r "$APK_PATH"

echo "Launching $APP_ID/.MainActivity..."
"$ADB_BIN" -s "$DEVICE_SERIAL" shell am start -n "$APP_ID/.MainActivity"

echo "Installation complete."
