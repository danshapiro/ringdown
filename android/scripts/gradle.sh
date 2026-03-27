#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:-} -Dfile.encoding=UTF-8"

LOCAL_PROPERTIES="${REPO_ROOT}/local.properties"
COMMON_GIT_DIR="$(git -C "${REPO_ROOT}" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
COMMON_REPO_ROOT=""
if [[ -n "${COMMON_GIT_DIR}" ]]; then
  COMMON_REPO_ROOT="$(dirname "${COMMON_GIT_DIR}")"
fi

read_local_property() {
  local key="$1"
  [[ -f "${LOCAL_PROPERTIES}" ]] || return 1
  local line
  line="$(grep -E "^${key}=" "${LOCAL_PROPERTIES}" | tail -n 1 || true)"
  [[ -n "${line}" ]] || return 1
  printf '%s\n' "${line#*=}"
}

to_host_path() {
  local raw="$1"
  if [[ "${raw}" =~ ^([A-Za-z]):[\\/](.*)$ ]]; then
    local drive="${BASH_REMATCH[1],,}"
    local remainder="${BASH_REMATCH[2]//\\//}"
    printf '/mnt/%s/%s\n' "${drive}" "${remainder}"
    return 0
  fi
  printf '%s\n' "${raw}"
}

resolve_existing_dir() {
  local candidate=""
  for candidate in "$@"; do
    [[ -n "${candidate}" ]] || continue
    if [[ -d "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

SHARED_ANDROID_ROOT="${COMMON_REPO_ROOT}/android"
DEFAULT_SDK_DIR="${REPO_ROOT}/.android-sdk"
DEFAULT_JDK_DIR="${REPO_ROOT}/.jdk/current"
LOCAL_SDK_DIR="$(read_local_property sdk.dir 2>/dev/null || true)"
LOCAL_JDK_DIR="$(read_local_property jdk.dir 2>/dev/null || true)"
TRANSLATED_LOCAL_SDK_DIR="$(to_host_path "${LOCAL_SDK_DIR}")"
TRANSLATED_LOCAL_JDK_DIR="$(to_host_path "${LOCAL_JDK_DIR}")"

RESOLVED_SDK_DIR="$(resolve_existing_dir \
  "${ANDROID_SDK_ROOT:-}" \
  "${ANDROID_HOME:-}" \
  "${SHARED_ANDROID_ROOT}/.android-sdk" \
  "${DEFAULT_SDK_DIR}" \
  "${TRANSLATED_LOCAL_SDK_DIR}" \
  || true)"
RESOLVED_JAVA_HOME="$(resolve_existing_dir \
  "${JAVA_HOME:-}" \
  "${SHARED_ANDROID_ROOT}/.jdk/current" \
  "${DEFAULT_JDK_DIR}" \
  "${TRANSLATED_LOCAL_JDK_DIR}/current" \
  "${REPO_ROOT}/.jdk" \
  "${TRANSLATED_LOCAL_JDK_DIR}" \
  || true)"

if [[ -z "${RESOLVED_SDK_DIR:-}" ]]; then
  echo "Android SDK not found. Set ANDROID_SDK_ROOT/ANDROID_HOME or install the repo SDK." >&2
  exit 1
fi

if [[ -z "${RESOLVED_JAVA_HOME:-}" ]]; then
  echo "Java SDK not found. Set JAVA_HOME or install the repo JDK." >&2
  exit 1
fi

export ANDROID_SDK_ROOT="${RESOLVED_SDK_DIR}"
export ANDROID_HOME="${RESOLVED_SDK_DIR}"
export JAVA_HOME="${RESOLVED_JAVA_HOME}"
export PATH="${JAVA_HOME}/bin:${ANDROID_SDK_ROOT}/platform-tools:${PATH}"
export GRADLE_OPTS="${GRADLE_OPTS:-} -Dorg.gradle.java.home=${JAVA_HOME}"

cd "$REPO_ROOT"

if [[ ! -x "./gradlew" ]]; then
  echo "Gradle wrapper not found. Did you clone the android project?" >&2
  exit 1
fi

./gradlew "$@"
