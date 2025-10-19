#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOS'
Usage: mock-server.sh <start|stop|status> [--port <port>] [--approval-threshold <n>] [--poll-after-seconds <seconds>]

Controls the lightweight registration mock backend used for integration tests.
EOS
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

COMMAND="$1"
shift

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="$REPO_ROOT/.tmp"
PID_FILE="$STATE_DIR/mock-server.pid"
LOG_FILE="$STATE_DIR/mock-server.log"

PORT=8899
APPROVAL_THRESHOLD=3
POLL_AFTER_SECONDS=5

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT="$2"
      shift 2
      ;;
    --approval-threshold)
      APPROVAL_THRESHOLD="$2"
      shift 2
      ;;
    --poll-after-seconds)
      POLL_AFTER_SECONDS="$2"
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

ensure_state_dir() {
  mkdir -p "$STATE_DIR"
}

is_running() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid=$(cat "$PID_FILE")
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  return 1
}

start_server() {
  if is_running; then
    echo "Mock server already running (PID $(cat "$PID_FILE"))."
    return 0
  fi

  ensure_state_dir
  local cmd=(python3 "$SCRIPT_DIR/mock_server.py" --host 127.0.0.1 "--port" "$PORT" "--approval-threshold" "$APPROVAL_THRESHOLD" "--poll-after-seconds" "$POLL_AFTER_SECONDS")

  if [[ -n "${UV_PROJECT_ENVIRONMENT:-}" ]]; then
    echo "Using existing UV project environment: $UV_PROJECT_ENVIRONMENT"
  fi

  echo "Starting mock server on http://127.0.0.1:$PORT (threshold=$APPROVAL_THRESHOLD, pollAfter=$POLL_AFTER_SECONDS)…"
  nohup "${cmd[@]}" >"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  sleep 0.5
  if is_running; then
    echo "Mock server started. Logs: $LOG_FILE"
  else
    echo "Failed to launch mock server. Check $LOG_FILE for details." >&2
    exit 1
  fi
}

stop_server() {
  if ! is_running; then
    echo "Mock server is not running."
    return 0
  fi

  local pid
  pid=$(cat "$PID_FILE")
  echo "Stopping mock server (PID $pid)…"
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "Stopped."
}

status_server() {
  if is_running; then
    echo "Mock server running (PID $(cat "$PID_FILE")) on http://127.0.0.1:$PORT"
    if [[ -f "$LOG_FILE" ]]; then
      tail -n 1 "$LOG_FILE" || true
    fi
  else
    echo "Mock server is not running."
  fi
}

case "$COMMAND" in
  start)
    start_server
    ;;
  stop)
    stop_server
    ;;
  status)
    status_server
    ;;
  *)
    echo "Unknown command: $COMMAND" >&2
    usage
    exit 1
    ;;
esac
