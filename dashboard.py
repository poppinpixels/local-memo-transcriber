#!/usr/bin/env python3
"""Web dashboard for the local memo transcription pipeline.

Serves a self-contained HTML dashboard that shows real-time pipeline
status, queue, history, and system info.  Zero external dependencies
beyond the Python standard library.

Usage::

    python3 dashboard.py --config ~/LocalMemoTranscriber/config.env
    python3 dashboard.py --config ~/LocalMemoTranscriber/config.env --port 9888
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

SUPPORTED_EXTENSIONS = {".m4a", ".mp3", ".wav", ".mp4", ".aac"}


def read_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value.startswith(('"', "'")) and value.endswith(('"', "'")) and len(value) >= 2:
            value = value[1:-1]
        data[key.strip()] = os.path.expandvars(value)
    return data


def scan_directory(directory: Path, extensions: set[str] | None = None) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    if not directory.is_dir():
        return files
    for entry in sorted(directory.iterdir()):
        if entry.name.startswith(".") or not entry.is_file():
            continue
        if extensions and entry.suffix.lower() not in extensions:
            continue
        try:
            stat = entry.stat()
            files.append({
                "name": entry.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            })
        except OSError:
            continue
    return files


def read_log_tail(path: Path, lines: int = 30) -> list[str]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text.strip().splitlines()[-lines:]
    except OSError:
        return []


def build_api_response(config_env: dict[str, str], config_path: Path) -> dict[str, Any]:
    def resolve(name: str, default: str = "") -> str:
        return os.environ.get(name, config_env.get(name, default)).strip()

    def resolve_path(name: str, default: str) -> Path:
        return Path(resolve(name, default)).expanduser().resolve()

    watch_dir = resolve_path("WATCH_DIR", str(Path.home() / "LocalMemoTranscriber" / "inbox"))
    transcripts_dir = resolve_path("TRANSCRIPTS_DIR", str(Path.home() / "LocalMemoTranscriber" / "transcripts"))
    done_dir = resolve_path("DONE_DIR", str(Path.home() / "LocalMemoTranscriber" / "done"))
    failed_dir = resolve_path("FAILED_DIR", str(Path.home() / "LocalMemoTranscriber" / "failed"))
    log_dir = resolve_path("LOG_DIR", str(Path.home() / "LocalMemoTranscriber" / "logs"))

    status_raw = resolve("STATUS_FILE", "")
    status_file = Path(status_raw).expanduser().resolve() if status_raw else None

    status: dict[str, Any] = {}
    if status_file and status_file.is_file():
        try:
            status = json.loads(status_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "model_id": resolve("MODEL_ID", "openai/whisper-large-v3"),
            "language": resolve("LANGUAGE", "") or "auto",
            "device_preference": resolve("DEVICE_PREFERENCE", "auto"),
            "poll_interval_seconds": int(resolve("POLL_INTERVAL_SECONDS", "1800")),
            "chunk_length_seconds": int(resolve("CHUNK_LENGTH_SECONDS", "30")),
            "output_formats": resolve("OUTPUT_FORMATS", "txt,json,srt"),
            "watch_dir": str(watch_dir),
            "transcripts_dir": str(transcripts_dir),
            "done_dir": str(done_dir),
            "failed_dir": str(failed_dir),
        },
        "watcher": status.get("watcher", {}),
        "pipeline": status.get("pipeline", {}),
        "history": status.get("history", []),
        "queue": scan_directory(watch_dir, SUPPORTED_EXTENSIONS),
        "done_files": scan_directory(done_dir)[-20:],
        "failed_files": scan_directory(failed_dir),
        "transcript_count": len([
            f for f in transcripts_dir.iterdir()
            if f.is_file() and f.suffix == ".txt"
        ]) if transcripts_dir.is_dir() else 0,
        "logs": {
            "runtime": read_log_tail(log_dir / "runtime.log"),
            "error": read_log_tail(log_dir / "error.log", lines=15),
        },
    }


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Memo Transcriber</title>
<style>
:root {
  --bg: #0f1117;
  --surface: #1a1d27;
  --surface2: #232736;
  --border: #2e3348;
  --text: #e4e6f0;
  --text2: #8b90a5;
  --accent: #6c8cff;
  --accent2: #4a6aef;
  --green: #3dd68c;
  --green-dim: #1a3d2e;
  --red: #f06;
  --red-dim: #3d1a2a;
  --yellow: #fbbf24;
  --yellow-dim: #3d351a;
  --radius: 10px;
  --mono: 'SF Mono', 'Menlo', 'Monaco', 'Consolas', monospace;
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  line-height: 1.5;
}
.container { max-width: 1100px; margin: 0 auto; padding: 24px 20px; }

/* Header */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 28px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border);
}
.header h1 {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
}
.header h1 span { color: var(--accent); }
.status-badge {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 500;
  padding: 6px 14px;
  border-radius: 20px;
  background: var(--surface);
  border: 1px solid var(--border);
}
.status-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--text2);
}
.status-dot.active { background: var(--green); box-shadow: 0 0 8px var(--green); }
.status-dot.processing { background: var(--accent); box-shadow: 0 0 8px var(--accent); animation: pulse 1.5s infinite; }
.status-dot.stopped { background: var(--red); }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* Grid */
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.grid .full { grid-column: 1 / -1; }

/* Cards */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
}
.card-title {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text2);
  margin-bottom: 14px;
}

/* Processing card */
.processing-file {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 4px;
  word-break: break-all;
}
.processing-step {
  color: var(--text2);
  font-size: 13px;
  margin-bottom: 12px;
}
.progress-bar {
  width: 100%;
  height: 6px;
  background: var(--surface2);
  border-radius: 3px;
  overflow: hidden;
  margin-bottom: 8px;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent2), var(--accent));
  border-radius: 3px;
  transition: width 0.5s ease;
}
.progress-info {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--text2);
}
.idle-message {
  color: var(--text2);
  font-size: 14px;
}

/* Queue */
.queue-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.queue-item:last-child { border-bottom: none; }
.queue-name { word-break: break-all; }
.queue-size { color: var(--text2); white-space: nowrap; margin-left: 12px; }
.empty-state {
  color: var(--text2);
  font-size: 13px;
  font-style: italic;
}

/* History */
.history-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.history-item:last-child { border-bottom: none; }
.history-icon {
  width: 20px; height: 20px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px;
  flex-shrink: 0;
}
.history-icon.done { background: var(--green-dim); color: var(--green); }
.history-icon.failed { background: var(--red-dim); color: var(--red); }
.history-details { flex: 1; min-width: 0; }
.history-name { font-weight: 500; word-break: break-all; }
.history-meta { color: var(--text2); font-size: 12px; }
.history-time { color: var(--text2); font-size: 12px; white-space: nowrap; }

/* Info grid */
.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.info-item label {
  display: block;
  font-size: 11px;
  color: var(--text2);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 2px;
}
.info-item span {
  font-size: 14px;
  font-family: var(--mono);
  font-weight: 500;
}

/* Logs */
.log-box {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px;
  font-family: var(--mono);
  font-size: 11px;
  line-height: 1.7;
  max-height: 260px;
  overflow-y: auto;
  color: var(--text2);
  white-space: pre-wrap;
  word-break: break-all;
}
.log-box::-webkit-scrollbar { width: 6px; }
.log-box::-webkit-scrollbar-track { background: transparent; }
.log-box::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

/* Stats bar */
.stats-bar {
  display: flex;
  gap: 24px;
  margin-bottom: 20px;
}
.stat {
  display: flex;
  flex-direction: column;
}
.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--accent);
  line-height: 1;
}
.stat-label {
  font-size: 11px;
  color: var(--text2);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-top: 4px;
}

/* Countdown */
.countdown { color: var(--text2); font-size: 13px; margin-top: 6px; }

/* Responsive */
@media (max-width: 700px) {
  .grid { grid-template-columns: 1fr; }
  .info-grid { grid-template-columns: 1fr; }
  .stats-bar { flex-wrap: wrap; gap: 16px; }
}

/* Refresh indicator */
.refresh-dot {
  width: 6px; height: 6px;
  border-radius: 50%;
  background: var(--accent);
  opacity: 0;
  transition: opacity 0.2s;
  margin-left: 8px;
}
.refresh-dot.flash { opacity: 1; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1><span>Memo</span> Transcriber</h1>
    <div style="display:flex;align-items:center;">
      <div class="status-badge">
        <div class="status-dot" id="statusDot"></div>
        <span id="statusText">Loading...</span>
      </div>
      <div class="refresh-dot" id="refreshDot"></div>
    </div>
  </div>

  <div class="stats-bar" id="statsBar"></div>

  <div class="grid">
    <!-- Current Processing -->
    <div class="card full">
      <div class="card-title">Currently Processing</div>
      <div id="processingContent">
        <div class="idle-message">Waiting for data...</div>
      </div>
    </div>

    <!-- Queue -->
    <div class="card">
      <div class="card-title">Queue <span id="queueCount"></span></div>
      <div id="queueContent">
        <div class="empty-state">No files in queue</div>
      </div>
    </div>

    <!-- System Info -->
    <div class="card">
      <div class="card-title">System</div>
      <div id="systemContent" class="info-grid"></div>
    </div>

    <!-- History -->
    <div class="card full">
      <div class="card-title">Recent Activity</div>
      <div id="historyContent">
        <div class="empty-state">No activity yet</div>
      </div>
    </div>

    <!-- Logs -->
    <div class="card full">
      <div class="card-title">Runtime Log</div>
      <div id="logContent" class="log-box">Loading...</div>
    </div>
  </div>
</div>

<script>
const STEP_LABELS = {
  idle: 'Idle',
  moving: 'Moving file to workspace',
  normalizing: 'Normalizing audio (ffmpeg)',
  detecting_silence: 'Detecting silence points',
  loading_model: 'Loading Whisper model',
  transcribing: 'Transcribing audio',
  writing_output: 'Writing output files',
  archiving: 'Archiving to done folder',
};

const STEP_WEIGHTS = {
  moving: 2,
  normalizing: 5,
  detecting_silence: 3,
  loading_model: 15,
  transcribing: 70,
  writing_output: 3,
  archiving: 2,
};

function stepProgress(state, chunkIndex, chunkTotal) {
  const steps = Object.keys(STEP_WEIGHTS);
  const idx = steps.indexOf(state);
  if (idx === -1) return 0;
  let base = 0;
  for (let i = 0; i < idx; i++) base += STEP_WEIGHTS[steps[i]];
  let stepPct = STEP_WEIGHTS[state] || 0;
  if (state === 'transcribing' && chunkTotal > 0) {
    stepPct = stepPct * (chunkIndex / chunkTotal);
  }
  return Math.min(100, base + stepPct);
}

function formatBytes(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

function formatDuration(seconds) {
  if (!seconds || seconds < 0) return '-';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m === 0) return s + 's';
  return m + 'm ' + s + 's';
}

function timeAgo(isoStr) {
  if (!isoStr) return '';
  const diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function countdownTo(isoStr) {
  if (!isoStr) return '';
  const diff = (new Date(isoStr).getTime() - Date.now()) / 1000;
  if (diff <= 0) return 'any moment';
  return 'in ' + formatDuration(diff);
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function render(data) {
  // Status badge
  const dot = document.getElementById('statusDot');
  const txt = document.getElementById('statusText');
  const wState = (data.watcher && data.watcher.state) || 'unknown';
  const pState = (data.pipeline && data.pipeline.state) || 'idle';

  if (pState !== 'idle') {
    dot.className = 'status-dot processing';
    txt.textContent = 'Processing';
  } else if (['started', 'scanning', 'sleeping'].includes(wState)) {
    dot.className = 'status-dot active';
    txt.textContent = 'Service Active';
  } else if (wState === 'stopped') {
    dot.className = 'status-dot stopped';
    txt.textContent = 'Service Stopped';
  } else {
    dot.className = 'status-dot';
    txt.textContent = wState || 'Unknown';
  }

  // Stats bar
  const statsBar = document.getElementById('statsBar');
  const transcriptCount = data.transcript_count || 0;
  const queueLen = (data.queue || []).length;
  const failedLen = (data.failed_files || []).length;
  const doneHistory = (data.history || []).filter(h => h.status === 'done');
  statsBar.innerHTML = `
    <div class="stat"><div class="stat-value">${transcriptCount}</div><div class="stat-label">Transcripts</div></div>
    <div class="stat"><div class="stat-value">${queueLen}</div><div class="stat-label">In Queue</div></div>
    <div class="stat"><div class="stat-value">${failedLen}</div><div class="stat-label">Failed</div></div>
    <div class="stat"><div class="stat-value">${doneHistory.length}</div><div class="stat-label">Recent</div></div>
  `;

  // Processing
  const pc = document.getElementById('processingContent');
  if (pState && pState !== 'idle') {
    const p = data.pipeline;
    const progress = stepProgress(pState, p.chunk_index || 0, p.chunk_total || 0);
    let chunkInfo = '';
    if (pState === 'transcribing' && p.chunk_total) {
      chunkInfo = ` (chunk ${p.chunk_index || '?'}/${p.chunk_total})`;
    }
    let durationInfo = '';
    if (p.duration_seconds) {
      durationInfo = `<span>Audio: ${formatDuration(p.duration_seconds)}</span>`;
    }
    let elapsed = '';
    if (p.started_at) {
      const secs = (Date.now() - new Date(p.started_at).getTime()) / 1000;
      elapsed = `<span>Elapsed: ${formatDuration(secs)}</span>`;
    }
    pc.innerHTML = `
      <div class="processing-file">${escapeHtml(p.original_name || p.file || 'Unknown')}</div>
      <div class="processing-step">${STEP_LABELS[pState] || pState}${chunkInfo}</div>
      <div class="progress-bar"><div class="progress-fill" style="width:${progress}%"></div></div>
      <div class="progress-info">${durationInfo}${elapsed}<span>${Math.round(progress)}%</span></div>
    `;
  } else {
    let nextPoll = '';
    if (data.watcher && data.watcher.next_poll_at) {
      nextPoll = `<div class="countdown">Next scan ${countdownTo(data.watcher.next_poll_at)}</div>`;
    }
    pc.innerHTML = `<div class="idle-message">No file currently being processed</div>${nextPoll}`;
  }

  // Queue
  const qc = document.getElementById('queueContent');
  const queueCountEl = document.getElementById('queueCount');
  const queue = data.queue || [];
  queueCountEl.textContent = queue.length ? `(${queue.length})` : '';
  if (queue.length === 0) {
    qc.innerHTML = '<div class="empty-state">Inbox is empty</div>';
  } else {
    qc.innerHTML = queue.map(f => `
      <div class="queue-item">
        <span class="queue-name">${escapeHtml(f.name)}</span>
        <span class="queue-size">${formatBytes(f.size_bytes)}</span>
      </div>
    `).join('');
  }

  // System info
  const sc = document.getElementById('systemContent');
  const cfg = data.config || {};
  const w = data.watcher || {};
  const poll = cfg.poll_interval_seconds;
  sc.innerHTML = `
    <div class="info-item"><label>Model</label><span>${escapeHtml(cfg.model_id || '-')}</span></div>
    <div class="info-item"><label>Device</label><span>${escapeHtml(cfg.device_preference || '-')}</span></div>
    <div class="info-item"><label>Language</label><span>${escapeHtml(cfg.language || '-')}</span></div>
    <div class="info-item"><label>Poll Interval</label><span>${poll ? formatDuration(poll) : '-'}</span></div>
    <div class="info-item"><label>Chunk Length</label><span>${cfg.chunk_length_seconds || '-'}s</span></div>
    <div class="info-item"><label>Formats</label><span>${escapeHtml(cfg.output_formats || '-')}</span></div>
  `;

  // History
  const hc = document.getElementById('historyContent');
  const history = data.history || [];
  if (history.length === 0) {
    hc.innerHTML = '<div class="empty-state">No activity yet</div>';
  } else {
    hc.innerHTML = history.slice(0, 15).map(h => {
      const isDone = h.status === 'done';
      const icon = isDone ? '&#10003;' : '&#10007;';
      const cls = isDone ? 'done' : 'failed';
      let meta = '';
      if (isDone) {
        const parts = [];
        if (h.duration_seconds) parts.push(formatDuration(h.duration_seconds) + ' audio');
        if (h.processing_seconds) parts.push(formatDuration(h.processing_seconds) + ' processing');
        meta = parts.join(' &rarr; ');
      } else {
        meta = escapeHtml(h.error || 'Unknown error');
      }
      return `
        <div class="history-item">
          <div class="history-icon ${cls}">${icon}</div>
          <div class="history-details">
            <div class="history-name">${escapeHtml(h.original_name || h.basename || '-')}</div>
            <div class="history-meta">${meta}</div>
          </div>
          <div class="history-time">${timeAgo(h.completed_at)}</div>
        </div>
      `;
    }).join('');
  }

  // Logs
  const lc = document.getElementById('logContent');
  const lines = (data.logs && data.logs.runtime) || [];
  if (lines.length === 0) {
    lc.textContent = 'No log entries yet.';
  } else {
    lc.textContent = lines.join('\n');
    lc.scrollTop = lc.scrollHeight;
  }
}

async function refresh() {
  try {
    const resp = await fetch('/api/status');
    const data = await resp.json();
    render(data);
    const dot = document.getElementById('refreshDot');
    dot.classList.add('flash');
    setTimeout(() => dot.classList.remove('flash'), 300);
  } catch (err) {
    console.error('Refresh failed:', err);
  }
}

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>"""


def make_handler(config_env: dict[str, str], config_path: Path) -> type:
    class DashboardHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            if self.path == "/api/status":
                self._send_json(build_api_response(config_env, config_path))
            elif self.path in ("/", "/index.html"):
                self._send_html(DASHBOARD_HTML)
            else:
                self.send_error(404)

        def _send_json(self, data: dict[str, Any]) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return DashboardHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="Dashboard for the local memo transcription pipeline.")
    parser.add_argument("--config", required=True, help="Path to config.env")
    parser.add_argument("--port", type=int, default=9888, help="HTTP port (default: 9888)")
    parser.add_argument("--no-open", action="store_true", help="Don't open browser automatically")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    if not config_path.is_file():
        print(f"Config file not found: {config_path}", file=sys.stderr)
        return 1

    config_env = read_env_file(config_path)

    handler_class = make_handler(config_env, config_path)
    server = HTTPServer(("127.0.0.1", args.port), handler_class)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Dashboard running at {url}")
    print("Press Ctrl+C to stop.\n")

    if not args.no_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
        server.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
