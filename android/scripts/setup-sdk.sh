#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: setup-sdk.sh --api <level> --build-tools <version> [options]

Provision the Android SDK command-line tools and install the requested platform
components. All downloads are cached under the SDK root so subsequent runs are fast.

Required arguments:
  --api <level>           Android API level to install (for example: 35)
  --build-tools <version> Build tools version to install (for example: 35.0.0)

Optional arguments:
  --sdk-root <path>             Directory for the Android SDK (default: <repo>/android/.android-sdk or ANDROID_SDK_ROOT)
  --commandline-version <rev>   Numeric revision of command line tools (default: 11076708)
  --jdk-version <version>       Temurin JDK major version to install if Java is missing (default: 21)
  --jdk-root <path>             Directory to install the managed JDK into (default: <repo>/android/.jdk)
  -h, --help                    Show this help message
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

require_command() {
  local cmd=$1
  command -v "$cmd" >/dev/null 2>&1 || die "Required command '$cmd' not found in PATH"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

API_LEVEL=""
BUILD_TOOLS=""
SDK_ROOT="${ANDROID_SDK_ROOT:-$ANDROID_ROOT/.android-sdk}"
CMDLINE_TOOLS_REVISION="11076708"
JDK_VERSION="21"
JDK_ROOT="$ANDROID_ROOT/.jdk"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api)
      [[ $# -ge 2 ]] || die "Missing value for --api"
      API_LEVEL="$2"
      shift 2
      ;;
    --build-tools)
      [[ $# -ge 2 ]] || die "Missing value for --build-tools"
      BUILD_TOOLS="$2"
      shift 2
      ;;
    --sdk-root)
      [[ $# -ge 2 ]] || die "Missing value for --sdk-root"
      SDK_ROOT="$2"
      shift 2
      ;;
    --commandline-version)
      [[ $# -ge 2 ]] || die "Missing value for --commandline-version"
      CMDLINE_TOOLS_REVISION="$2"
      shift 2
      ;;
    --jdk-version)
      [[ $# -ge 2 ]] || die "Missing value for --jdk-version"
      JDK_VERSION="$2"
      shift 2
      ;;
    --jdk-root)
      [[ $# -ge 2 ]] || die "Missing value for --jdk-root"
      JDK_ROOT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

[[ -n "$API_LEVEL" ]] || die "--api is required"
[[ -n "$BUILD_TOOLS" ]] || die "--build-tools is required"

case "$OSTYPE" in
  linux*) PLATFORM="linux" ;;
  darwin*) PLATFORM="mac" ;;
  *) die "Unsupported platform '$OSTYPE'. Only Linux and macOS are supported." ;;
esac

require_command curl
require_command unzip

mkdir -p "$SDK_ROOT"
export ANDROID_SDK_ROOT="$SDK_ROOT"
export ANDROID_HOME="$SDK_ROOT"

CMDLINE_DIR="$SDK_ROOT/cmdline-tools"
SDKMANAGER="$CMDLINE_DIR/latest/bin/sdkmanager"

download_cmdline_tools() {
  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN

  local archive="commandlinetools-${PLATFORM}-${CMDLINE_TOOLS_REVISION}_latest.zip"
  local url="https://dl.google.com/android/repository/${archive}"

  echo "Downloading Android command line tools (revision ${CMDLINE_TOOLS_REVISION})..."
  curl -fsSL "$url" -o "$tmpdir/$archive"

  echo "Extracting command line tools..."
  unzip -q "$tmpdir/$archive" -d "$tmpdir"

  mkdir -p "$CMDLINE_DIR"
  rm -rf "$CMDLINE_DIR/latest"
  mv "$tmpdir/cmdline-tools" "$CMDLINE_DIR/latest"

  # Ensure sdkmanager is executable
  chmod +x "$CMDLINE_DIR/latest/bin/"*
  trap - RETURN
}

if [[ ! -x "$SDKMANAGER" ]]; then
  download_cmdline_tools
else
  echo "Android command line tools already present at $CMDLINE_DIR"
fi

echo "Using Android SDK root: $SDK_ROOT"
echo "Using sdkmanager: $SDKMANAGER"

packages=(
  "platform-tools"
  "platforms;android-${API_LEVEL}"
  "build-tools;${BUILD_TOOLS}"
)

run_sdkmanager() {
  set +o pipefail
  yes | "$SDKMANAGER" --sdk_root="$SDK_ROOT" "$@"
  local status=$?
  set -o pipefail
  return "$status"
}

install_jdk() {
  mkdir -p "$JDK_ROOT"
  local archive_name="jdk-${JDK_VERSION}-linux-x64.tar.gz"
  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN

  local url="https://api.adoptium.net/v3/binary/latest/${JDK_VERSION}/ga/linux/x64/jdk/hotspot/normal/eclipse"
  echo "Downloading Temurin JDK ${JDK_VERSION}..."
  curl -fsSL "$url" -o "$tmpdir/$archive_name"

  echo "Extracting JDK..."
  tar -xzf "$tmpdir/$archive_name" -C "$tmpdir"
  local extracted
  extracted=$(find "$tmpdir" -maxdepth 1 -type d -name "jdk-${JDK_VERSION}*" -print -quit)
  [[ -n "$extracted" ]] || die "Failed to locate extracted JDK directory."

  rm -rf "$JDK_ROOT/current"
  mkdir -p "$JDK_ROOT"
  mv "$extracted" "$JDK_ROOT/current"

  export JAVA_HOME="$JDK_ROOT/current"
  export PATH="$JAVA_HOME/bin:$PATH"

  trap - RETURN
}

if ! command -v java >/dev/null 2>&1; then
  install_jdk
else
  JAVA_BIN=$(command -v java)
  JAVA_HOME="${JAVA_HOME:-$(dirname "$(dirname "$JAVA_BIN")")}"
  export JAVA_HOME
fi

export PATH="$JAVA_HOME/bin:$PATH"

echo "Installing packages: ${packages[*]}..."
run_sdkmanager "${packages[@]}" || die "Failed to install requested Android components."

echo "Accepting licenses..."
if ! run_sdkmanager --licenses >/dev/null; then
  die "Failed to accept Android SDK licenses."
fi

cat <<EOF

Android SDK setup complete.
Environment variables to export:
  export ANDROID_SDK_ROOT="$SDK_ROOT"
  export ANDROID_HOME="$SDK_ROOT"
  export JAVA_HOME="${JAVA_HOME}"
  export PATH="\$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:\$ANDROID_SDK_ROOT/platform-tools:\$PATH"

EOF
