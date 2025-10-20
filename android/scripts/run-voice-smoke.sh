#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [--backend <URL>]" >&2
}

BACKEND_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend)
      BACKEND_URL="${2:-}"
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
ARGS=(./gradlew :app:connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.ringdown.voice.VoiceMvpSuite)

if [[ -n "${BACKEND_URL}" ]]; then
  ARGS+=("-Pandroid.testInstrumentationRunnerArguments.backendUrl=${BACKEND_URL}")
fi

"${SCRIPT_DIR}/gradle.sh" "${ARGS[@]}"
