#!/usr/bin/env bash
set -euo pipefail

# Resolve repository root (script lives in android/scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [[ "${OS:-}" == "Windows_NT" ]]; then
  export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-.venv}"
else
  export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-.venv-wsl}"
fi

cd "${REPO_ROOT}"

uv run python -m app.mobile.smoke "$@"
