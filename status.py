"""Status tracking for the local memo transcription pipeline.

Writes pipeline and watcher state to a JSON status file so the dashboard
(and other tools) can display real-time progress.

Usage from Python::

    tracker = StatusTracker(Path("~/LocalMemoTranscriber/status.json"))
    tracker.update_pipeline(state="transcribing", file="memo.m4a", chunk_index=2, chunk_total=4)
    tracker.pipeline_done(basename="2026-03-25_0935_memo", duration=45.2, processing_time=120.0)

Usage from bash (CLI)::

    python3 status.py /path/to/status.json watcher --state sleeping --next-poll-at "2026-03-25T14:30:00"
    python3 status.py /path/to/status.json pipeline --state transcribing --file memo.m4a --chunk-index 2 --chunk-total 4
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_HISTORY = 50


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_status(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_status(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class StatusTracker:
    """Thread-safe status writer for the transcription pipeline."""

    def __init__(self, status_file: Path) -> None:
        self.status_file = status_file

    def _update(self, section: str, data: dict[str, Any]) -> None:
        status = _read_status(self.status_file)
        existing = status.get(section, {})
        existing.update(data)
        status[section] = existing
        _write_status(self.status_file, status)

    # ── Watcher state ──────────────────────────────────────────────

    def update_watcher(
        self,
        *,
        state: str,
        pid: int | None = None,
        poll_interval_seconds: int | None = None,
        next_poll_at: str | None = None,
        files_in_queue: int | None = None,
    ) -> None:
        data: dict[str, Any] = {"state": state, "updated_at": _now_iso()}
        if pid is not None:
            data["pid"] = pid
        if poll_interval_seconds is not None:
            data["poll_interval_seconds"] = poll_interval_seconds
        if next_poll_at is not None:
            data["next_poll_at"] = next_poll_at
        if files_in_queue is not None:
            data["files_in_queue"] = files_in_queue
        self._update("watcher", data)

    def watcher_stopped(self) -> None:
        self._update("watcher", {"state": "stopped", "updated_at": _now_iso(), "pid": None})

    # ── Pipeline state ─────────────────────────────────────────────

    def update_pipeline(
        self,
        *,
        state: str,
        file: str | None = None,
        original_name: str | None = None,
        basename: str | None = None,
        duration_seconds: float | None = None,
        chunk_index: int | None = None,
        chunk_total: int | None = None,
        device: str | None = None,
        model_id: str | None = None,
        error: str | None = None,
    ) -> None:
        data: dict[str, Any] = {"state": state, "updated_at": _now_iso()}
        if state in ("moving", "normalizing"):
            data["started_at"] = _now_iso()
        if file is not None:
            data["file"] = file
        if original_name is not None:
            data["original_name"] = original_name
        if basename is not None:
            data["basename"] = basename
        if duration_seconds is not None:
            data["duration_seconds"] = duration_seconds
        if chunk_index is not None:
            data["chunk_index"] = chunk_index
        if chunk_total is not None:
            data["chunk_total"] = chunk_total
        if device is not None:
            data["device"] = device
        if model_id is not None:
            data["model_id"] = model_id
        if error is not None:
            data["error"] = error
        self._update("pipeline", data)

    def pipeline_idle(self) -> None:
        status = _read_status(self.status_file)
        status["pipeline"] = {"state": "idle", "updated_at": _now_iso()}
        _write_status(self.status_file, status)

    def pipeline_done(
        self,
        *,
        original_name: str,
        basename: str,
        duration_seconds: float,
        processing_seconds: float,
    ) -> None:
        status = _read_status(self.status_file)
        status["pipeline"] = {"state": "idle", "updated_at": _now_iso()}

        history = status.get("history", [])
        history.insert(0, {
            "original_name": original_name,
            "basename": basename,
            "completed_at": _now_iso(),
            "duration_seconds": round(duration_seconds, 1),
            "processing_seconds": round(processing_seconds, 1),
            "status": "done",
        })
        status["history"] = history[:MAX_HISTORY]
        _write_status(self.status_file, status)

    def pipeline_failed(
        self,
        *,
        original_name: str,
        error: str,
    ) -> None:
        status = _read_status(self.status_file)
        status["pipeline"] = {"state": "idle", "updated_at": _now_iso()}

        history = status.get("history", [])
        history.insert(0, {
            "original_name": original_name,
            "completed_at": _now_iso(),
            "status": "failed",
            "error": error,
        })
        status["history"] = history[:MAX_HISTORY]
        _write_status(self.status_file, status)


# ── CLI interface (for bash watcher) ─────────────────────────────

def _cli() -> int:
    parser = argparse.ArgumentParser(description="Update transcription pipeline status.")
    parser.add_argument("status_file", help="Path to status.json")
    sub = parser.add_subparsers(dest="command", required=True)

    w = sub.add_parser("watcher", help="Update watcher state")
    w.add_argument("--state", required=True, choices=["started", "scanning", "processing", "sleeping", "stopped"])
    w.add_argument("--pid", type=int)
    w.add_argument("--poll-interval", type=int)
    w.add_argument("--next-poll-at")
    w.add_argument("--files-in-queue", type=int)

    p = sub.add_parser("pipeline", help="Update pipeline state")
    p.add_argument("--state", required=True)
    p.add_argument("--file")
    p.add_argument("--original-name")
    p.add_argument("--basename")
    p.add_argument("--duration-seconds", type=float)
    p.add_argument("--chunk-index", type=int)
    p.add_argument("--chunk-total", type=int)
    p.add_argument("--device")
    p.add_argument("--model-id")
    p.add_argument("--error")

    p_idle = sub.add_parser("pipeline-idle", help="Set pipeline to idle")
    p_done = sub.add_parser("pipeline-done", help="Record completed transcription")
    p_done.add_argument("--original-name", required=True)
    p_done.add_argument("--basename", required=True)
    p_done.add_argument("--duration-seconds", type=float, required=True)
    p_done.add_argument("--processing-seconds", type=float, required=True)

    p_fail = sub.add_parser("pipeline-failed", help="Record failed transcription")
    p_fail.add_argument("--original-name", required=True)
    p_fail.add_argument("--error", required=True)

    args = parser.parse_args()
    tracker = StatusTracker(Path(args.status_file).expanduser().resolve())

    if args.command == "watcher":
        if args.state == "stopped":
            tracker.watcher_stopped()
        else:
            tracker.update_watcher(
                state=args.state,
                pid=args.pid,
                poll_interval_seconds=args.poll_interval,
                next_poll_at=args.next_poll_at,
                files_in_queue=args.files_in_queue,
            )
    elif args.command == "pipeline":
        tracker.update_pipeline(
            state=args.state,
            file=args.file,
            original_name=args.original_name,
            basename=args.basename,
            duration_seconds=args.duration_seconds,
            chunk_index=args.chunk_index,
            chunk_total=args.chunk_total,
            device=args.device,
            model_id=args.model_id,
            error=args.error,
        )
    elif args.command == "pipeline-idle":
        tracker.pipeline_idle()
    elif args.command == "pipeline-done":
        tracker.pipeline_done(
            original_name=args.original_name,
            basename=args.basename,
            duration_seconds=args.duration_seconds,
            processing_seconds=args.processing_seconds,
        )
    elif args.command == "pipeline-failed":
        tracker.pipeline_failed(
            original_name=args.original_name,
            error=args.error,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
