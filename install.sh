#!/bin/bash
set -euo pipefail

PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_BASE_DEFAULT="$HOME/LocalMemoTranscriber"
RUNTIME_BASE="${BASE_DIR_OVERRIDE:-$RUNTIME_BASE_DEFAULT}"
CONFIG_DEST="${CONFIG_DEST_OVERRIDE:-$RUNTIME_BASE/config.env}"
LOG_BOOTSTRAP_DIR="$RUNTIME_BASE/logs"
mkdir -p "$LOG_BOOTSTRAP_DIR"
INSTALL_LOG="$LOG_BOOTSTRAP_DIR/install.log"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$INSTALL_LOG"
}

python_supports_minimum() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

python_is_preferred() {
  "$1" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)' >/dev/null 2>&1
}

python_version() {
  "$1" -c 'import sys; print(".".join(str(part) for part in sys.version_info[:3]))'
}

choose_python() {
  local candidate resolved

  if [[ -n "${PYTHON_BIN_OVERRIDE:-}" ]]; then
    if [[ -x "${PYTHON_BIN_OVERRIDE}" ]] && python_supports_minimum "${PYTHON_BIN_OVERRIDE}"; then
      printf '%s\n' "${PYTHON_BIN_OVERRIDE}"
      return 0
    fi
    echo "PYTHON_BIN_OVERRIDE is set but unusable or older than Python 3.11: ${PYTHON_BIN_OVERRIDE}" >&2
    return 1
  fi

  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      resolved="$(command -v "$candidate")"
      if python_supports_minimum "$resolved"; then
        printf '%s\n' "$resolved"
        return 0
      fi
    fi
  done

  return 1
}

render_plist() {
  local template_path="$1"
  local output_path="$2"
  local watch_script="$3"
  local config_path="$4"
  local stdout_path="$5"
  local stderr_path="$6"
  local project_dir="$7"
  local python_cmd="$8"

  "$python_cmd" - "$template_path" "$output_path" "$watch_script" "$config_path" "$stdout_path" "$stderr_path" "$project_dir" <<'PY'
from pathlib import Path
import sys

template = Path(sys.argv[1]).read_text(encoding='utf-8')
replacements = {
    '__WATCH_SCRIPT__': sys.argv[3],
    '__CONFIG_PATH__': sys.argv[4],
    '__STDOUT_PATH__': sys.argv[5],
    '__STDERR_PATH__': sys.argv[6],
    '__PROJECT_DIR__': sys.argv[7],
}
for key, value in replacements.items():
    template = template.replace(key, value)
Path(sys.argv[2]).write_text(template, encoding='utf-8')
PY
}

if ! PYTHON_CMD="$(choose_python)"; then
  echo "A compatible Python runtime was not found. Install Python 3.11+ first (3.11-3.13 preferred for PyTorch wheels)." >&2
  exit 1
fi

PYTHON_VERSION="$(python_version "$PYTHON_CMD")"
log "Using Python: $PYTHON_CMD ($PYTHON_VERSION)"

if ! python_is_preferred "$PYTHON_CMD"; then
  log "Warning: Python $PYTHON_VERSION is outside the preferred 3.11-3.13 range. If torch install fails, install python3.13 and rerun with PYTHON_BIN_OVERRIDE."
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required but was not found in PATH." >&2
  exit 1
fi

if ! command -v ffprobe >/dev/null 2>&1; then
  echo "ffprobe is required but was not found in PATH." >&2
  exit 1
fi

mkdir -p "$(dirname "$CONFIG_DEST")"
if [[ ! -f "$CONFIG_DEST" ]]; then
  if [[ -n "${BASE_DIR_OVERRIDE:-}" ]]; then
    "$PYTHON_CMD" - "$SCRIPT_DIR/config.env.example" "$CONFIG_DEST" "$RUNTIME_BASE" <<'PY'
from pathlib import Path
import sys

template_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
runtime_base = sys.argv[3]
content = template_path.read_text(encoding='utf-8')
content = content.replace('$HOME/LocalMemoTranscriber', runtime_base)
output_path.write_text(content, encoding='utf-8')
PY
  else
    cp "$SCRIPT_DIR/config.env.example" "$CONFIG_DEST"
  fi
  log "Created config: $CONFIG_DEST"
else
  log "Reusing existing config: $CONFIG_DEST"
fi

set -a
# shellcheck disable=SC1090
source "$CONFIG_DEST"
set +a

WATCH_DIR="${WATCH_DIR_OVERRIDE:-$WATCH_DIR}"
TRANSCRIPTS_DIR="${TRANSCRIPTS_DIR_OVERRIDE:-$TRANSCRIPTS_DIR}"
DONE_DIR="${DONE_DIR_OVERRIDE:-$DONE_DIR}"
FAILED_DIR="${FAILED_DIR_OVERRIDE:-$FAILED_DIR}"
LOG_DIR="${LOG_DIR_OVERRIDE:-$LOG_DIR}"
TMP_DIR="${TMP_DIR_OVERRIDE:-$TMP_DIR}"
VENV_DIR="${VENV_DIR_OVERRIDE:-$VENV_DIR}"
PLIST_LABEL="${PLIST_LABEL_OVERRIDE:-local.memo-transcriber}"
if [[ -n "${BASE_DIR_OVERRIDE:-}" && -z "${PLIST_DEST_OVERRIDE:-}" ]]; then
  PLIST_DEST="$RUNTIME_BASE/$PLIST_LABEL.plist"
else
  PLIST_DEST="${PLIST_DEST_OVERRIDE:-$HOME/Library/LaunchAgents/$PLIST_LABEL.plist}"
fi
PLIST_TEMPLATE="$SCRIPT_DIR/launchd/memo-transcriber.plist"
WATCH_SCRIPT="$SCRIPT_DIR/watch_and_transcribe.sh"
TRANSCRIBER_SCRIPT="$SCRIPT_DIR/transcribe_hviske.py"
WATCH_STDOUT="$LOG_DIR/launchd.out.log"
WATCH_STDERR="$LOG_DIR/launchd.err.log"

mkdir -p "$WATCH_DIR" "$TRANSCRIPTS_DIR" "$DONE_DIR" "$FAILED_DIR" "$LOG_DIR" "$TMP_DIR" "$VENV_DIR" "$(dirname "$PLIST_DEST")"

if [[ ! -d "$VENV_DIR/bin" ]]; then
  log "Creating virtual environment in $VENV_DIR"
  "$PYTHON_CMD" -m venv "$VENV_DIR"
else
  log "Reusing virtual environment in $VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Virtual environment python was not created correctly: $VENV_PYTHON" >&2
  exit 1
fi

if [[ "${SKIP_PIP_INSTALL:-0}" != "1" ]]; then
  log "Installing Python dependencies"
  "$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel >> "$INSTALL_LOG" 2>&1
  "$VENV_PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt" >> "$INSTALL_LOG" 2>&1
else
  log "Skipping pip install because SKIP_PIP_INSTALL=1"
fi

chmod +x "$WATCH_SCRIPT" "$TRANSCRIBER_SCRIPT" "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/dashboard.py"

if [[ ! -f "$PLIST_TEMPLATE" ]]; then
  echo "launchd template missing: $PLIST_TEMPLATE" >&2
  exit 1
fi

render_plist "$PLIST_TEMPLATE" "$PLIST_DEST" "$WATCH_SCRIPT" "$CONFIG_DEST" "$WATCH_STDOUT" "$WATCH_STDERR" "$SCRIPT_DIR" "$PYTHON_CMD"
log "Rendered launchd plist: $PLIST_DEST"

if command -v plutil >/dev/null 2>&1; then
  plutil -lint "$PLIST_DEST" >> "$INSTALL_LOG" 2>&1
  log "Validated launchd plist with plutil"
fi

log "Running dry-run config check"
WATCH_DIR_OVERRIDE="$WATCH_DIR" \
TRANSCRIPTS_DIR_OVERRIDE="$TRANSCRIPTS_DIR" \
DONE_DIR_OVERRIDE="$DONE_DIR" \
FAILED_DIR_OVERRIDE="$FAILED_DIR" \
LOG_DIR_OVERRIDE="$LOG_DIR" \
TMP_DIR_OVERRIDE="$TMP_DIR" \
VENV_DIR_OVERRIDE="$VENV_DIR" \
"$VENV_PYTHON" "$TRANSCRIBER_SCRIPT" --config "$CONFIG_DEST" --dry-run >> "$INSTALL_LOG" 2>&1

if [[ "${SKIP_LAUNCHD:-0}" != "1" ]]; then
  if launchctl bootout "gui/$(id -u)" "$PLIST_DEST" >/dev/null 2>&1; then
    log "Unloaded existing launchd job"
  fi

  if launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST" >/dev/null 2>&1; then
    log "Loaded launchd job via bootstrap"
  elif launchctl load -w "$PLIST_DEST" >/dev/null 2>&1; then
    log "Loaded launchd job via load -w"
  else
    log "Could not load launchd job automatically. The plist was still rendered successfully."
  fi

  launchctl kickstart -k "gui/$(id -u)/$PLIST_LABEL" >/dev/null 2>&1 || true
else
  log "Skipping launchd load because SKIP_LAUNCHD=1"
fi

DASHBOARD_PORT="${DASHBOARD_PORT:-9888}"

cat <<EOF
Install complete.

Project directory: $SCRIPT_DIR
Config file:       $CONFIG_DEST
Inbox:             $WATCH_DIR
Transcripts:       $TRANSCRIPTS_DIR
Done:              $DONE_DIR
Failed:            $FAILED_DIR
Logs:              $LOG_DIR
Venv:              $VENV_DIR
launchd plist:     $PLIST_DEST

Dashboard:
  $VENV_PYTHON $SCRIPT_DIR/dashboard.py --config $CONFIG_DEST --port $DASHBOARD_PORT

Manual checks:
  tail -f "$LOG_DIR/runtime.log"
  tail -f "$LOG_DIR/error.log"
  launchctl print "gui/$(id -u)/$PLIST_LABEL" | head
EOF
