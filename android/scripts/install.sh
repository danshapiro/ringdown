#!/usr/bin/env bash
set -euo pipefail

UV_PROJECT_ENVIRONMENT=${UV_PROJECT_ENVIRONMENT:-.venv-wsl}
export UV_PROJECT_ENVIRONMENT

uv run python android/scripts/install.py "$@"
