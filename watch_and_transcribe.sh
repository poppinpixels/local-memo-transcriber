#!/bin/bash
set -euo pipefail

PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.env"
RUN_ONCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_FILE="$2"
      shift 2
      ;;
    --once)
      RUN_ONCE=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Config file not found: $CONFIG_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a

: "${WATCH_DIR:?Missing WATCH_DIR in config}"
: "${TRANSCRIPTS_DIR:?Missing TRANSCRIPTS_DIR in config}"
: "${DONE_DIR:?Missing DONE_DIR in config}"
: "${FAILED_DIR:?Missing FAILED_DIR in config}"
: "${LOG_DIR:?Missing LOG_DIR in config}"
: "${TMP_DIR:?Missing TMP_DIR in config}"
: "${VENV_DIR:?Missing VENV_DIR in config}"

WATCH_DIR="${WATCH_DIR_OVERRIDE:-$WATCH_DIR}"
TRANSCRIPTS_DIR="${TRANSCRIPTS_DIR_OVERRIDE:-$TRANSCRIPTS_DIR}"
DONE_DIR="${DONE_DIR_OVERRIDE:-$DONE_DIR}"
FAILED_DIR="${FAILED_DIR_OVERRIDE:-$FAILED_DIR}"
LOG_DIR="${LOG_DIR_OVERRIDE:-$LOG_DIR}"
TMP_DIR="${TMP_DIR_OVERRIDE:-$TMP_DIR}"
VENV_DIR="${VENV_DIR_OVERRIDE:-$VENV_DIR}"
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS_OVERRIDE:-${POLL_INTERVAL_SECONDS:-300}}"
STABILITY_WAIT_SECONDS="${STABILITY_WAIT_SECONDS_OVERRIDE:-${STABILITY_WAIT_SECONDS:-15}}"
PYTHON_BIN="${PYTHON_BIN_OVERRIDE:-${PYTHON_BIN:-}}"
STATUS_FILE="${STATUS_FILE_OVERRIDE:-${STATUS_FILE:-}}"

if [[ -z "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$VENV_DIR/bin/python"
fi

STATUS_SCRIPT="$SCRIPT_DIR/status.py"

update_status() {
  if [[ -n "$STATUS_FILE" && -f "$STATUS_SCRIPT" ]]; then
    "$PYTHON_BIN" "$STATUS_SCRIPT" "$STATUS_FILE" "$@" 2>/dev/null || true
  fi
}

mkdir -p "$WATCH_DIR" "$TRANSCRIPTS_DIR" "$DONE_DIR" "$FAILED_DIR" "$LOG_DIR" "$TMP_DIR"

LOG_FILE="$LOG_DIR/runtime.log"
ERROR_FILE="$LOG_DIR/error.log"
LOCK_DIR="$TMP_DIR/.watcher.lock"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE"
}

log_error() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$ERROR_FILE" >&2
}

cleanup() {
  update_status watcher --state stopped
  rm -f "$LOCK_DIR/pid" >/dev/null 2>&1 || true
  rmdir "$LOCK_DIR" >/dev/null 2>&1 || true
}

lowercase() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

get_file_signature() {
  stat -f '%z:%m' "$1"
}

is_supported_audio() {
  case "$(lowercase "$1")" in
    *.m4a|*.mp3|*.wav|*.mp4|*.aac) return 0 ;;
    *) return 1 ;;
  esac
}

is_locally_downloaded() {
  # iCloud stubs report a logical size but use 0 disk blocks.
  local file="$1"
  local blocks
  blocks=$(stat -f '%b' "$file" 2>/dev/null) || return 1
  [[ "$blocks" -gt 0 ]]
}

is_stable_file() {
  local file="$1"
  local before after

  if [[ ! -f "$file" ]]; then
    return 1
  fi

  # Skip iCloud stubs that haven't been downloaded locally yet.
  if ! is_locally_downloaded "$file"; then
    return 1
  fi

  before="$(get_file_signature "$file")" || return 1
  sleep "$STABILITY_WAIT_SECONDS"

  if [[ ! -f "$file" ]]; then
    return 1
  fi

  after="$(get_file_signature "$file")" || return 1
  [[ "$before" == "$after" ]]
}

count_queue_files() {
  local count=0
  while IFS= read -r -d '' f; do
    if is_supported_audio "$f"; then
      count=$((count + 1))
    fi
  done < <(find "$WATCH_DIR" -maxdepth 1 -type f -not -name '.*' -print0 2>/dev/null)
  printf '%d' "$count"
}

process_available_files() {
  update_status watcher --state scanning --files-in-queue "$(count_queue_files)"

  while IFS= read -r -d '' file; do
    if ! is_supported_audio "$file"; then
      continue
    fi

    if ! is_stable_file "$file"; then
      log "Skipping unstable file for now: $file"
      continue
    fi

    update_status watcher --state processing
    log "Processing: $file"
    if "$PYTHON_BIN" "$SCRIPT_DIR/transcribe_hviske.py" --config "$CONFIG_FILE" --input "$file"; then
      log "Completed: $file"
    else
      log_error "Failed: $file"
    fi
  done < <(find "$WATCH_DIR" -maxdepth 1 -type f -not -name '.*' -print0)
}

if [[ ! -x "$PYTHON_BIN" ]]; then
  log_error "Python executable not found or not executable: $PYTHON_BIN"
  exit 1
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "Watcher already running. Exiting this instance."
  exit 0
fi

trap cleanup EXIT INT TERM
printf '%s\n' "$$" > "$LOCK_DIR/pid"
log "Watcher started with config: $CONFIG_FILE"
update_status watcher --state started --pid "$$" --poll-interval "$POLL_INTERVAL_SECONDS"

while true; do
  process_available_files

  if [[ "$RUN_ONCE" -eq 1 ]]; then
    log "Run-once mode complete."
    break
  fi

  NEXT_POLL="$(date -v "+${POLL_INTERVAL_SECONDS}S" '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || date -d "+${POLL_INTERVAL_SECONDS} seconds" '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || echo "")"
  update_status watcher --state sleeping --next-poll-at "$NEXT_POLL" --files-in-queue "$(count_queue_files)"
  update_status pipeline-idle
  sleep "$POLL_INTERVAL_SECONDS"
done
