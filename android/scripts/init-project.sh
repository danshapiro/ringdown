#!/usr/bin/env bash
set -euo pipefail

# This script bootstraps the Android workspace. It is idempotent and safe to rerun.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GRADLE_WRAPPER_JAR="$REPO_ROOT/gradle/wrapper/gradle-wrapper.jar"

if [[ ! -f "$GRADLE_WRAPPER_JAR" ]]; then
  echo "Gradle wrapper jar missing at $GRADLE_WRAPPER_JAR" >&2
  echo "Download the Gradle distribution and place the wrapper jar before running this script." >&2
  exit 1
fi

cat <<'EOF'
Android project scaffolding is already present.
Run `bash android/scripts/gradle.sh tasks` to verify the wrapper.
EOF
