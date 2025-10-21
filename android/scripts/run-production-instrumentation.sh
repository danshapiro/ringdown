#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF' >&2
Usage: run-production-instrumentation.sh --backend <URL> [--device <serial>] [--device-id <id>] [--skip-build] [--skip-install] [--approve-device]

Builds (unless skipped), installs the debug + androidTest APKs, and runs the
RegistrationSuite and VoiceMvpSuite instrumentation tests against the supplied
backend. Intended to simplify Phase 3 production validation on a tethered
device or emulator.

Environment fallbacks:
  ANDROID_SERIAL            default device serial if --device not supplied
  RINGDOWN_BACKEND_URL      default backend URL if --backend not supplied
  RINGDOWN_DEVICE_ID_OVERRIDE default instrumentation device id
  PYTHON_BIN                interpreter used for approve_device.py (default: python)
EOF
}

BACKEND_URL="${RINGDOWN_BACKEND_URL:-}"
DEVICE_SERIAL="${ANDROID_SERIAL:-}"
DEVICE_ID_OVERRIDE="${RINGDOWN_DEVICE_ID_OVERRIDE:-instrumentation-device}"
SKIP_BUILD=0
SKIP_INSTALL=0
APPROVE_DEVICE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend)
      [[ $# -ge 2 ]] || { echo "Error: missing value for --backend" >&2; usage; exit 1; }
      BACKEND_URL="${2:-}"
      shift 2
      ;;
    --device)
      [[ $# -ge 2 ]] || { echo "Error: missing value for --device" >&2; usage; exit 1; }
      DEVICE_SERIAL="${2:-}"
      shift 2
      ;;
    --device-id)
      [[ $# -ge 2 ]] || { echo "Error: missing value for --device-id" >&2; usage; exit 1; }
      DEVICE_ID_OVERRIDE="${2:-}"
      shift 2
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    --skip-install)
      SKIP_INSTALL=1
      shift
      ;;
    --approve-device)
      APPROVE_DEVICE=1
      shift
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

if [[ -z "$BACKEND_URL" ]]; then
  echo "Error: backend URL is required. Provide --backend or set RINGDOWN_BACKEND_URL." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$ANDROID_ROOT/app"
ADB_BIN="${ANDROID_SDK_ROOT:-$ANDROID_ROOT/.android-sdk}/platform-tools/adb"
PYTHON_BIN="${PYTHON_BIN:-python}"

[[ -x "$ADB_BIN" ]] || { echo "Error: adb not found at $ADB_BIN" >&2; exit 1; }
[[ -d "$PROJECT_ROOT" ]] || { echo "Error: Android project not initialized at $PROJECT_ROOT" >&2; exit 1; }

if [[ $SKIP_BUILD -eq 0 ]]; then
  "$SCRIPT_DIR/gradle.sh" ./gradlew :app:assembleDebug :app:assembleDebugAndroidTest
fi

if [[ -z "$DEVICE_SERIAL" ]]; then
  DEVICE_SERIAL=$("$ADB_BIN" devices | awk 'NR>1 && $2=="device" {print $1; exit}')
fi

[[ -n "$DEVICE_SERIAL" ]] || { echo "Error: no connected device detected. Supply --device or set ANDROID_SERIAL." >&2; exit 1; }

if [[ $APPROVE_DEVICE -eq 1 ]]; then
  APPROVE_SCRIPT="$SCRIPT_DIR/approve_device.py"
  if [[ ! -f "$APPROVE_SCRIPT" ]]; then
    echo "Error: approve_device.py not found at $APPROVE_SCRIPT" >&2
    exit 1
  fi
  APPROVE_ARGS=(--device "$DEVICE_SERIAL")
  if [[ -n "$DEVICE_ID_OVERRIDE" ]]; then
    APPROVE_ARGS+=(--device-id "$DEVICE_ID_OVERRIDE")
  fi
  if [[ -n "${DEPLOY_PROJECT_ID:-}" ]]; then
    APPROVE_ARGS+=(--project-id "$DEPLOY_PROJECT_ID")
  elif [[ -n "${LIVE_TEST_PROJECT_ID:-}" ]]; then
    APPROVE_ARGS+=(--project-id "$LIVE_TEST_PROJECT_ID")
  fi
  # Avoid unnecessary redeploys for the shared instrumentation override.
  if [[ "$DEVICE_ID_OVERRIDE" == "instrumentation-device" ]]; then
    APPROVE_ARGS+=(--skip-deploy)
  fi
  echo "Approving instrumentation device..."
  "$PYTHON_BIN" "$APPROVE_SCRIPT" "${APPROVE_ARGS[@]}"
fi

if [[ $SKIP_INSTALL -eq 0 ]]; then
  DEBUG_APK="$PROJECT_ROOT/app/build/outputs/apk/debug/app-debug.apk"
  TEST_APK="$PROJECT_ROOT/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk"
  [[ -f "$DEBUG_APK" ]] || { echo "Error: debug APK not found at $DEBUG_APK" >&2; exit 1; }
  [[ -f "$TEST_APK" ]] || { echo "Error: androidTest APK not found at $TEST_APK" >&2; exit 1; }

  "$ADB_BIN" -s "$DEVICE_SERIAL" install -r "$DEBUG_APK"
  "$ADB_BIN" -s "$DEVICE_SERIAL" install -r "$TEST_APK"
fi

# Clean Gradle caches that frequently remain locked on Windows hosts.
CACHE_DIRS=(
  "$PROJECT_ROOT/app/build/tmp"
  "$PROJECT_ROOT/app/build/kotlin"
  "$PROJECT_ROOT/app/build/generated"
  "$PROJECT_ROOT/app/build/intermediates"
  "$PROJECT_ROOT/app/build/reports/androidTests"
  "$PROJECT_ROOT/app/build/outputs/connected_android_test_additional_output"
  "$PROJECT_ROOT/app/build/outputs/androidTest-results"
)

for cache_dir in "${CACHE_DIRS[@]}"; do
  if [[ -d "$cache_dir" ]]; then
    echo "Cleaning $cache_dir"
    rm -rf "$cache_dir"
  fi
done

run_instrumentation() {
  local suite_class="$1"
  echo "Running $suite_class on $DEVICE_SERIAL..."
  "$ADB_BIN" -s "$DEVICE_SERIAL" shell am instrument -w \
    -e backendUrl "$BACKEND_URL" \
    -e deviceIdOverride "$DEVICE_ID_OVERRIDE" \
    -e class "$suite_class" \
    com.ringdown.test/androidx.test.runner.AndroidJUnitRunner
}

run_instrumentation "com.ringdown.registration.RegistrationSuite"
run_instrumentation "com.ringdown.voice.VoiceMvpSuite"

echo "Production instrumentation complete."
