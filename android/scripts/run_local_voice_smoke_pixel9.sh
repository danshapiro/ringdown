#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="${SCRIPT_DIR}/voice_smoke_profiles/pixel9.json"
HARNESS="${SCRIPT_DIR}/run_local_voice_smoke.py"

if [[ ! -f "${HARNESS}" ]]; then
  echo "Harness script not found at ${HARNESS}" >&2
  exit 1
fi

if [[ ! -f "${PROFILE}" ]]; then
  echo "Pixel 9 profile missing at ${PROFILE}" >&2
  exit 1
fi

CMD=(python3 "${HARNESS}" --profile "${PROFILE}")
if [[ $# -gt 0 ]]; then
  CMD+=("$@")
fi

exec "${CMD[@]}"
