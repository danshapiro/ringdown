#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF' >&2
Usage: run-voice-smoke.sh [--backend <URL>] [--device-id <ID>] [--auth-token <TOKEN>]

Runs the live reconnect Android instrumentation smoke against a connected
device/emulator. The backend URL must be supplied via --backend or the
RINGDOWN_BACKEND_URL environment variable. The mobile auth token is optional:
the wrapper uses --auth-token or LIVE_TEST_MOBILE_AUTH_TOKEN when supplied,
and otherwise tries to discover the configured token from the local backend.
EOF
}

BACKEND_URL="${RINGDOWN_BACKEND_URL:-}"
DEFAULT_DEVICE_ID="instrumentation-device"
DEVICE_ID_OVERRIDE="${RINGDOWN_DEVICE_ID_OVERRIDE:-${LIVE_TEST_MOBILE_DEVICE_ID:-}}"
AUTH_TOKEN_OVERRIDE="${LIVE_TEST_MOBILE_AUTH_TOKEN:-}"
AUTH_TOKEN_DEVICE_FILE="/data/local/tmp/ringdown-live-auth-token.txt"
AUTH_TOKEN_DEVICE_FILE_PUSHED=0

run_adb() {
  local cmd=("${ADB_BIN}")
  if [[ -n "${ANDROID_SERIAL:-}" ]]; then
    cmd+=("-s" "${ANDROID_SERIAL}")
  fi
  cmd+=("$@")
  "${cmd[@]}"
}

cleanup_auth_token_file() {
  if [[ "${AUTH_TOKEN_DEVICE_FILE_PUSHED}" == "1" ]]; then
    run_adb shell rm -f "${AUTH_TOKEN_DEVICE_FILE}" >/dev/null 2>&1 || true
  fi
}

push_auth_token_file() {
  printf '%s' "${AUTH_TOKEN_OVERRIDE}" | run_adb shell "cat > ${AUTH_TOKEN_DEVICE_FILE}" >/dev/null
  AUTH_TOKEN_DEVICE_FILE_PUSHED=1
}

trap cleanup_auth_token_file EXIT

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
    --auth-token)
      [[ $# -ge 2 ]] || { echo "Error: missing value for --auth-token" >&2; usage; exit 1; }
      AUTH_TOKEN_OVERRIDE="${2:-}"
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

if [[ -z "${BACKEND_URL}" ]]; then
  echo "Error: backend URL is required. Provide --backend or set RINGDOWN_BACKEND_URL." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
RESOLVED_DEVICE_ID="${DEVICE_ID_OVERRIDE:-${DEFAULT_DEVICE_ID}}"

resolve_adb_bin() {
  if [[ -n "${RINGDOWN_ADB_BIN:-}" ]]; then
    printf '%s\n' "${RINGDOWN_ADB_BIN}"
    return
  fi

  local sdk_root="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-${REPO_ROOT}/android/.android-sdk}}"
  local candidate="${sdk_root}/platform-tools/adb"
  if [[ -x "${candidate}" ]]; then
    printf '%s\n' "${candidate}"
    return
  fi

  if command -v adb >/dev/null 2>&1; then
    printf '%s\n' "adb"
    return
  fi

  echo "Error: adb not found. Set RINGDOWN_ADB_BIN, ANDROID_SDK_ROOT, or add adb to PATH." >&2
  exit 1
}

ADB_BIN="$(resolve_adb_bin)"

if [[ -z "${AUTH_TOKEN_OVERRIDE}" ]]; then
  AUTH_TOKEN_OVERRIDE="$(
    cd "${REPO_ROOT}"
    UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-.venv-wsl}" \
      uv run python - "${BACKEND_URL}" "${RESOLVED_DEVICE_ID}" <<'PY'
import sys
from urllib.parse import urlparse

from app.mobile.smoke import prepare_local_smoke_device, resolve_remote_smoke_auth_token

base_url = sys.argv[1]
device_id = sys.argv[2]
host = (urlparse(base_url).hostname or "").strip().lower()
token = ""
if host in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}:
    token = prepare_local_smoke_device(device_id) or ""
if not token:
    token = resolve_remote_smoke_auth_token(
        base_url=base_url,
        device_id=device_id,
    ) or ""
print(token, end="")
PY
  )"
fi

ARGS=(
  ":app:connectedVoiceMvpAndroidTest"
  "-Pandroid.testInstrumentationRunnerArguments.class=com.ringdown.mobile.LiveServiceReconnectAndroidTest"
  "-Pandroid.testInstrumentationRunnerArguments.liveDeviceId=${RESOLVED_DEVICE_ID}"
)

if [[ -n "${AUTH_TOKEN_OVERRIDE}" ]]; then
  push_auth_token_file
fi

STAGING_BACKEND_BASE_URL="${BACKEND_URL}" \
  "${SCRIPT_DIR}/gradle.sh" "${ARGS[@]}"
