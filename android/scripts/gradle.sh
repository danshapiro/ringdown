#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: gradle.sh <command> [args...]

Wrapper that sets Android SDK and Java environments before delegating to the Gradle
wrapper inside android/app. Example:

  bash android/scripts/gradle.sh ./gradlew tasks
EOF
}

if [[ $# -eq 0 ]]; then
  usage
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$ANDROID_ROOT/app"

SDK_ROOT="${ANDROID_SDK_ROOT:-$ANDROID_ROOT/.android-sdk}"
JAVA_HOME_DEFAULT="$ANDROID_ROOT/.jdk/current"
JAVA_HOME="${JAVA_HOME:-$JAVA_HOME_DEFAULT}"

[[ -d "$SDK_ROOT" ]] || { echo "Error: Android SDK not found at $SDK_ROOT" >&2; exit 1; }
[[ -d "$JAVA_HOME" ]] || { echo "Error: JAVA_HOME directory does not exist at $JAVA_HOME" >&2; exit 1; }
[[ -d "$PROJECT_ROOT" ]] || { echo "Error: Android project not initialized at $PROJECT_ROOT" >&2; exit 1; }

export ANDROID_SDK_ROOT="$SDK_ROOT"
export ANDROID_HOME="$SDK_ROOT"
export JAVA_HOME
export JDK_HOME="$JAVA_HOME"
export PATH="$JAVA_HOME/bin:$ANDROID_SDK_ROOT/platform-tools:$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$PATH"
export ORG_GRADLE_JAVA_INSTALLATIONS_PATHS="$JAVA_HOME"

if [[ -z "${GRADLE_USER_HOME:-}" ]]; then
  PREFERRED_GRADLE_CACHE="$ANDROID_ROOT/.gradle-cache"
  if [[ "$PREFERRED_GRADLE_CACHE" == /mnt/* || "$PREFERRED_GRADLE_CACHE" == /cygdrive/* ]]; then
    ALT_CACHE="$HOME/.gradle-ringdown"
    mkdir -p "$ALT_CACHE"
    if [[ ! -e "$PREFERRED_GRADLE_CACHE" ]]; then
      ln -s "$ALT_CACHE" "$PREFERRED_GRADLE_CACHE"
    fi
    export GRADLE_USER_HOME="$ALT_CACHE"
  else
    export GRADLE_USER_HOME="$PREFERRED_GRADLE_CACHE"
  fi
else
  export GRADLE_USER_HOME
fi

mkdir -p "$GRADLE_USER_HOME"

(
  cd "$PROJECT_ROOT"
  exec "$@"
)
