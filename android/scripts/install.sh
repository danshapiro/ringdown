#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install.sh --device <serial> [--variant <gradleTask>] [--apk-path <file>]

Builds the specified variant (default: :app:assembleDebug) and installs the resulting
APK on the provided device serial via adb.
EOF
}

DEVICE_SERIAL=""
VARIANT=":app:assembleDebug"
APK_PATH_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --device)
      [[ $# -ge 2 ]] || { echo "Error: missing value for --device" >&2; exit 1; }
      DEVICE_SERIAL="$2"
      shift 2
      ;;
    --variant)
      [[ $# -ge 2 ]] || { echo "Error: missing value for --variant" >&2; exit 1; }
      VARIANT="$2"
      shift 2
      ;;
    --apk-path)
      [[ $# -ge 2 ]] || { echo "Error: missing value for --apk-path" >&2; exit 1; }
      APK_PATH_OVERRIDE="$2"
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

[[ -n "$DEVICE_SERIAL" ]] || { echo "Error: --device is required" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$ANDROID_ROOT/app"
APP_ID="${APP_ID_OVERRIDE:-com.ringdown}"

[[ -d "$PROJECT_ROOT" ]] || { echo "Error: Android project not initialized. Run android/scripts/init-project.sh first." >&2; exit 1; }

determine_variant_keyword() {
  local task="$1"
  local name="${task##*:}"
  if [[ "$name" =~ ^assemble(.+)$ ]]; then
    local suffix="${BASH_REMATCH[1]}"
    printf '%s' "$(tr '[:upper:]' '[:lower:]' <<<"$suffix")"
  else
    printf ''
  fi
}

select_newest_file() {
  local latest_path=""
  local latest_mtime=0
  local path mtime
  for path in "$@"; do
    if [[ ! -f "$path" ]]; then
      continue
    fi
    if [[ "$OSTYPE" == darwin* ]]; then
      mtime=$(stat -f %m "$path")
    else
      mtime=$(stat -c %Y "$path")
    fi
    if (( mtime > latest_mtime )); then
      latest_mtime=$mtime
      latest_path="$path"
    fi
  done
  printf '%s' "$latest_path"
}

APK_OUTPUT_ROOT="$PROJECT_ROOT/app/build/outputs/apk"
BUILD_MARKER="$(mktemp)"
trap 'rm -f "$BUILD_MARKER"' EXIT
touch "$BUILD_MARKER"

"$SCRIPT_DIR/gradle.sh" ./gradlew "$VARIANT"

APK_PATH="$APK_PATH_OVERRIDE"

if [[ -z "$APK_PATH" ]]; then
  NEW_APKS=()
  while IFS= read -r apk_path; do
    [[ -n "$apk_path" ]] && NEW_APKS+=("$apk_path")
  done < <(find "$APK_OUTPUT_ROOT" -type f -name "*.apk" -newer "$BUILD_MARKER" 2>/dev/null | sort)

  if [[ ${#NEW_APKS[@]} -gt 1 ]]; then
    variant_keyword="$(determine_variant_keyword "$VARIANT")"
    if [[ -n "$variant_keyword" ]]; then
      FILTERED=()
      for apk in "${NEW_APKS[@]}"; do
        lower_basename="$(basename "$apk" | tr '[:upper:]' '[:lower:]')"
        if [[ "$lower_basename" == *"$variant_keyword"* ]]; then
          FILTERED+=("$apk")
        fi
      done
      if [[ ${#FILTERED[@]} -gt 0 ]]; then
        NEW_APKS=("${FILTERED[@]}")
      fi
    fi
  fi

  if [[ ${#NEW_APKS[@]} -eq 1 ]]; then
    APK_PATH="${NEW_APKS[0]}"
  elif [[ ${#NEW_APKS[@]} -gt 1 ]]; then
    APK_PATH="$(select_newest_file "${NEW_APKS[@]}")"
  fi

  if [[ -z "$APK_PATH" ]]; then
    DEFAULT_DEBUG_APK="$PROJECT_ROOT/app/build/outputs/apk/debug/app-debug.apk"
    if [[ -f "$DEFAULT_DEBUG_APK" ]]; then
      APK_PATH="$DEFAULT_DEBUG_APK"
    fi
  fi

  if [[ -z "$APK_PATH" ]]; then
    variant_name="${VARIANT##*:}"
    if [[ "$variant_name" =~ ^assemble(.+)$ ]]; then
      suffix="${BASH_REMATCH[1]}"
      # Split CamelCase suffix into parts (flavors + build type)
      old_ifs="$IFS"
      IFS=' '
      read -r -a parts <<<"$(sed -E 's/([A-Z][^A-Z]*)/ \1/g' <<<"$suffix")"
      IFS="$old_ifs"
      if (( ${#parts[@]} >= 1 )); then
        build_type="${parts[${#parts[@]}-1]}"
        build_type_lower="$(tr '[:upper:]' '[:lower:]' <<<"$build_type")"
        candidates=()
        if (( ${#parts[@]} == 1 )); then
          candidates+=("$PROJECT_ROOT/app/build/outputs/apk/$build_type_lower/app-$build_type_lower.apk")
          candidates+=("$PROJECT_ROOT/app/build/outputs/apk/$build_type_lower.apk")
        else
          flavors=("${parts[@]:0:${#parts[@]}-1}")
          flavor_camel=""
          flavor_hyphen=""
          for idx in "${!flavors[@]}"; do
            part="${flavors[$idx]}"
            lower_part="$(tr '[:upper:]' '[:lower:]' <<<"$part")"
            first_lower="$(tr '[:upper:]' '[:lower:]' <<<"${part:0:1}")"
            rest="${part:1}"
            if (( idx == 0 )); then
              flavor_camel="$first_lower$rest"
            else
              flavor_camel+="${part}"
            fi
            if [[ -n "$flavor_hyphen" ]]; then
              flavor_hyphen+="-$lower_part"
            else
              flavor_hyphen="$lower_part"
            fi
          done
          flavor_camel_lower="$(tr '[:upper:]' '[:lower:]' <<<"$flavor_camel")"
          flavor_compact="${flavor_hyphen//-/}"
          candidates+=("$PROJECT_ROOT/app/build/outputs/apk/$flavor_camel/$build_type_lower/app-$flavor_hyphen-$build_type_lower.apk")
          candidates+=("$PROJECT_ROOT/app/build/outputs/apk/$flavor_camel/$build_type_lower/app-$flavor_camel_lower-$build_type_lower.apk")
          candidates+=("$PROJECT_ROOT/app/build/outputs/apk/$flavor_compact/$build_type_lower/app-$flavor_hyphen-$build_type_lower.apk")
          candidates+=("$PROJECT_ROOT/app/build/outputs/apk/$build_type_lower/app-$flavor_hyphen-$build_type_lower.apk")
        fi
        for candidate in "${candidates[@]}"; do
          if [[ -f "$candidate" ]]; then
            APK_PATH="$candidate"
            break
          fi
        done
      fi
    fi
  fi
fi

[[ -n "$APK_PATH" ]] || { echo "Error: Unable to locate built APK. Use --apk-path to specify it explicitly." >&2; exit 1; }

[[ -f "$APK_PATH" ]] || { echo "Error: APK not found at $APK_PATH" >&2; exit 1; }

ADB_BIN="${ANDROID_SDK_ROOT:-$ANDROID_ROOT/.android-sdk}/platform-tools/adb"
[[ -x "$ADB_BIN" ]] || { echo "Error: adb not found. Ensure the SDK platform-tools are installed." >&2; exit 1; }

echo "Installing build to device $DEVICE_SERIAL..."
"$ADB_BIN" -s "$DEVICE_SERIAL" install -r "$APK_PATH"

echo "Launching $APP_ID/.MainActivity..."
"$ADB_BIN" -s "$DEVICE_SERIAL" shell am start -n "$APP_ID/.MainActivity"

echo "Installation complete."
