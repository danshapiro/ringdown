#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF' >&2
Usage: run-voice-smoke.sh [--backend <URL>] [--device-id <ID>]

Runs the VoiceMvpSuite against a connected device/emulator. The backend URL must
be supplied via --backend or the RINGDOWN_BACKEND_URL environment variable.
EOF
}

BACKEND_URL="${RINGDOWN_BACKEND_URL:-}"
DEVICE_ID_OVERRIDE="${RINGDOWN_DEVICE_ID_OVERRIDE:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend)
      [[ $# -ge 2 ]] || { echo "Error: missing value for --backend" >&2; usage; exit 1; }
      BACKEND_URL="${2:-}"
      shift 2
      ;;
    --device-id)
      [[ $# -ge 2 ]] || { echo "Error: missing value for --device-id" >&2; usage; exit 1; }
      DEVICE_ID_OVERRIDE="${2:-}"
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

if [[ -z "$BACKEND_URL" ]]; then
  echo "Error: backend URL is required. Provide --backend or set RINGDOWN_BACKEND_URL." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARGS=(
  ./gradlew
  :app:connectedDebugAndroidTest
  -Pandroid.testInstrumentationRunnerArguments.class=com.ringdown.voice.VoiceMvpSuite
  "-Pringdown.backendUrl=${BACKEND_URL}"
)

if [[ -n "$DEVICE_ID_OVERRIDE" ]]; then
  ARGS+=("-Pringdown.deviceIdOverride=${DEVICE_ID_OVERRIDE}")
fi

"${SCRIPT_DIR}/gradle.sh" "${ARGS[@]}"
