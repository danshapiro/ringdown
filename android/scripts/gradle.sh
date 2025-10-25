#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:-} -Dfile.encoding=UTF-8"

cd "$REPO_ROOT"

if [[ ! -x "./gradlew" ]]; then
  echo "Gradle wrapper not found. Did you clone the android project?" >&2
  exit 1
fi

./gradlew "$@"
