#!/usr/bin/env python3
"""Minimal HTTP server for receiving audio files from iPhone Shortcuts.

POST /upload  -> receives multipart file upload, writes atomically to inbox/
GET  /status  -> returns status.json content
GET  /        -> simple HTML status page

Atomic delivery: writes to inbox/.<name>.part, then os.rename() to inbox/<name>.
The daemon only sees complete files - no stability wait needed.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs

# ---------------------------------------------------------------------------
# Config (loaded once at startup)
# ---------------------------------------------------------------------------

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


def load_config(config_path: Path) -> dict[str, str]:
    env = read_env_file(config_path)
    port = int(os.environ.get("UPLOAD_PORT", env.get("UPLOAD_PORT", "9889")))
    return {
        "inbox_dir": os.environ.get("WATCH_DIR", env.get("WATCH_DIR", str(Path.home() / "LocalMemoTranscriber" / "inbox"))),
        "status_file": os.environ.get("STATUS_FILE", env.get("STATUS_FILE", str(Path.home() / "LocalMemoTranscriber" / "status.json"))),
        "port": str(port),
        "host": os.environ.get("UPLOAD_HOST", env.get("UPLOAD_HOST", "0.0.0.0")),
    }


CONFIG_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "LocalMemoTranscriber" / "config.env"
CFG = load_config(CONFIG_PATH)
INBOX = Path(str(CFG["inbox_dir"]))
INBOX.mkdir(parents=True, exist_ok=True)

SUPPORTED_EXTENSIONS = {".m4a", ".mp3", ".wav", ".mp4", ".aac"}


# ---------------------------------------------------------------------------
# Multipart parser (stdlib only, no Flask/FastAPI dependency)
# ---------------------------------------------------------------------------

def parse_multipart(body: bytes, boundary: str) -> dict[str, dict]:
    """Parse multipart/form-data. Returns {fieldname: {filename, content_type, data}}."""
    parts = {}
    delimiter = b"--" + boundary.encode()
    sections = body.split(delimiter)

    for section in sections:
        section = section.strip(b"\r\n")
        if not section or section == b"--":
            continue

        # Split headers from content
        if b"\r\n\r\n" in section:
            header_block, content = section.split(b"\r\n\r\n", 1)
        else:
            continue

        headers = {}
        for line in header_block.split(b"\r\n"):
            try:
                key, val = line.decode("utf-8", errors="replace").split(":", 1)
                headers[key.strip().lower()] = val.strip()
            except ValueError:
                continue

        # Parse Content-Disposition
        disposition = headers.get("content-disposition", "")
        filename = None
        field_name = None
        for part in disposition.split(";"):
            part = part.strip()
            if part.startswith("filename="):
                filename = part[len("filename="):].strip('"')
            elif part.startswith("name="):
                field_name = part[len("name="):].strip('"')

        if field_name:
            parts[field_name] = {
                "filename": filename,
                "content_type": headers.get("content-type", "application/octet-stream"),
                "data": content,
            }

    return parts


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class UploadHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Quiet logging - daemon handles the log
        pass

    def do_GET(self):
        if self.path == "/" or self.path == "/status":
            self.serve_status()
        else:
            self.send_error(404, "Not found")

    def do_POST(self):
        if self.path == "/upload" or self.path == "/":
            self.handle_upload()
        else:
            self.send_error(404, "Not found")

    def serve_status(self):
        status_path = Path(str(CFG["status_file"]))
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            status = {"error": "status.json not found"}

        # JSON API response
        if self.path == "/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(status, ensure_ascii=False, indent=2).encode("utf-8"))
            return

        # Simple HTML page for browser access
        watcher = status.get("watcher", {})
        pipeline = status.get("pipeline", {})
        history = status.get("history", [])

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Memo Transcriber</title>
<meta http-equiv="refresh" content="5">
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 700px; margin: 2rem auto; padding: 0 1rem; color: #333; }}
  h1 {{ font-size: 1.5rem; }}
  .status {{ padding: 1rem; border-radius: 8px; margin: 0.5rem 0; background: #f5f5f5; }}
  .state {{ font-weight: bold; color: #007; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ padding: 0.3rem 0; border-bottom: 1px solid #eee; font-size: 0.9rem; }}
  .done {{ color: #070; }}
  .failed {{ color: #700; }}
  a {{ color: #007; }}
</style>
</head>
<body>
<h1>Memo Transcriber</h1>
<div class="status">
  <span class="state">Watcher: {watcher.get("state", "unknown")}</span>
  {"files in queue" if watcher.get("files_in_queue", 0) > 0 else "queue empty"}
  {f"({watcher.get('files_in_queue', 0)} waiting)" if watcher.get("files_in_queue", 0) > 0 else ""}
  {f"polling every {watcher.get('poll_interval_seconds', '?')}s" if watcher.get("poll_interval_seconds") else ""}
</div>
<div class="status">
  <span class="state">Pipeline: {pipeline.get("state", "idle")}</span>
  {f"- {pipeline.get('file', '')}" if pipeline.get("file") else ""}
  {f"(chunk {pipeline.get('chunk_index', 0)}/{pipeline.get('chunk_total', 0)})" if pipeline.get("chunk_total") else ""}
</div>
<h2>History</h2>
<ul>
"""
        for entry in history[:10]:
            name = entry.get("original_name", entry.get("basename", "?"))
            status_cls = "done" if entry.get("status") == "done" else "failed"
            when = entry.get("completed_at", "?")
            html += f'<li class="{status_cls}">{when} - {name} - {entry.get("status", "?")}</li>\n'

        html += """</ul>
<p><a href="/status">JSON API</a> | Auto-refresh 5s</p>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def handle_upload(self):
        content_type = self.headers.get("Content-Type", "")

        if "multipart/form-data" not in content_type:
            self.send_error(400, "Expected multipart/form-data")
            return

        # Extract boundary
        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary="):].strip('"')
                break

        if not boundary:
            self.send_error(400, "No boundary in content type")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_error(400, "Empty request body")
            return

        # Read body
        body = self.rfile.read(content_length)

        # Parse multipart
        parts = parse_multipart(body, boundary)

        if "file" not in parts and "audio" not in parts:
            self.send_error(400, "No 'file' or 'audio' field in upload")
            return

        file_data = parts.get("file") or parts["audio"]
        filename = file_data["filename"] or f"upload_{os.getpid()}.m4a"

        # Sanitize filename
        safe_name = os.path.basename(filename)
        # Preserve Danish characters but remove problematic chars
        safe_name = safe_name.replace(" ", "-").replace("/", "-").replace("\\", "-")

        if not safe_name:
            safe_name = f"upload_{int(__import__('time').time())}.m4a"

        # Ensure supported extension
        ext = Path(safe_name).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            safe_name += ".m4a"

        # Atomic write: .part file, then rename
        final_path = INBOX / safe_name
        part_path = INBOX / f".{safe_name}.part"

        try:
            with open(part_path, "wb") as f:
                f.write(file_data["data"])
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename (on same filesystem)
            os.rename(str(part_path), str(final_path))
        except OSError as exc:
            # Clean up partial file
            part_path.unlink(missing_ok=True)
            self.send_error(500, f"Failed to write file: {exc}")
            return

        # Success response
        response = json.dumps({
            "status": "accepted",
            "filename": safe_name,
            "size_bytes": len(file_data["data"]),
            "message": f"File received. Transcription will start within 10 seconds."
        })

        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(response.encode("utf-8"))


def main():
    host: str = CFG["host"]
    port: int = int(CFG["port"])
    server = HTTPServer((host, port), UploadHandler)
    print(f"Upload server listening on {host}:{port}", flush=True)
    print(f"Inbox: {INBOX}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
