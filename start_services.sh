#!/bin/bash
# Launches both the upload server and the transcription daemon.
# Used by launchd so both processes are supervised under one agent.
# Compatible with macOS default bash 3.2.

set -euo pipefail

CONFIG_FILE="$HOME/LocalMemoTranscriber/config.env"
CODE_DIR="$HOME/projects/local-memo-transcriber"
VENV_PY="$HOME/LocalMemoTranscriber/venv/bin/python"

# Start upload server in background
"$VENV_PY" "$CODE_DIR/upload_server.py" "$CONFIG_FILE" &
UPLOAD_PID=$!

# Start daemon in background
"$VENV_PY" "$CODE_DIR/daemon.py" --config "$CONFIG_FILE" &
DAEMON_PID=$!

# Wait for either to exit; if one dies, kill the other and exit
# (launchd KeepAlive will restart the whole thing)
cleanup() {
    kill "$UPLOAD_PID" "$DAEMON_PID" 2>/dev/null || true
    wait "$UPLOAD_PID" "$DAEMON_PID" 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM EXIT

# Poll until either process exits
while true; do
    sleep 5
    if ! kill -0 "$UPLOAD_PID" 2>/dev/null; then
        echo "Upload server exited, shutting down" >&2
        break
    fi
    if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
        echo "Daemon exited, shutting down" >&2
        break
    fi
done

exit 0
