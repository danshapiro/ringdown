#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
python3 "/run_local_voice_smoke.py" --profile pixel9 ""
