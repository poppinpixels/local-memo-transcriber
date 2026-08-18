#!/usr/bin/env python3
"""Daemon: watches inbox/, processes audio files through transcribe_hviske.py.

Replaces watch_and_transcribe.sh with:
- flock for locking (auto-released by kernel on process death)
- processing/ directory for crash-safe file lifecycle
- 3x retry with exponential backoff
- macOS notification on success and permanent failure
- 10s poll interval (configurable)
- crash recovery: moves files left in processing/ back to inbox on startup

The transcription engine (transcribe_hviske.py) is called as a subprocess.
The model loads, transcribes, and exits per file - keeping memory free between jobs.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Config loading (same env-file parser as transcribe_hviske.py)
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


class DaemonConfig:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        env = read_env_file(config_path)

        def get(name: str, default: str = "") -> str:
            return os.environ.get(name, env.get(name, default)).strip()

        def get_int(name: str, default: int) -> int:
            try:
                return int(get(name, str(default)))
            except ValueError:
                return default

        def get_float(name: str, default: float) -> float:
            try:
                return float(get(name, str(default)))
            except ValueError:
                return default

        def path_value(name: str, default: str) -> Path:
            return Path(get(name, default)).expanduser().resolve()

        self.watch_dir = path_value("WATCH_DIR", str(Path.home() / "LocalMemoTranscriber" / "inbox"))
        self.transcripts_dir = path_value("TRANSCRIPTS_DIR", str(Path.home() / "LocalMemoTranscriber" / "transcripts"))
        self.done_dir = path_value("DONE_DIR", str(Path.home() / "LocalMemoTranscriber" / "done"))
        self.failed_dir = path_value("FAILED_DIR", str(Path.home() / "LocalMemoTranscriber" / "failed"))
        self.log_dir = path_value("LOG_DIR", str(Path.home() / "LocalMemoTranscriber" / "logs"))
        self.tmp_dir = path_value("TMP_DIR", str(Path.home() / "LocalMemoTranscriber" / "tmp"))
        self.processing_dir = self.tmp_dir / "processing"
        self.venv_python = path_value("VENV_DIR", str(Path.home() / "LocalMemoTranscriber" / "venv")) / "bin" / "python"
        self.transcribe_script = config_path.parent / "transcribe_hviske.py"
        if not self.transcribe_script.exists():
            # Try the code directory (projects/local-memo-transcriber)
            code_dir = Path(get("CODE_DIR", str(Path.home() / "projects" / "local-memo-transcriber")))
            self.transcribe_script = code_dir / "transcribe_hviske.py"
        self.status_script = self.transcribe_script.parent / "status.py"
        self.status_file = path_value("STATUS_FILE", str(Path.home() / "LocalMemoTranscriber" / "status.json"))

        # Optional local Obsidian source-bank integration. Disabled unless the
        # runtime config explicitly names Morten's vault and routing rules.
        self.obsidian_ingest_enabled = get("OBSIDIAN_INGEST_ENABLED", "false").lower() == "true"
        self.obsidian_vault_dir = path_value("OBSIDIAN_VAULT_DIR", str(Path.home() / "Obsidian" / "SecondBrain"))
        self.obsidian_transcripts_dir = path_value(
            "OBSIDIAN_TRANSCRIPTS_DIR", str(self.obsidian_vault_dir / "raw" / "transcriptions")
        )
        self.obsidian_links_file = path_value(
            "OBSIDIAN_LINKS_FILE", str(Path.home() / "LocalMemoTranscriber" / "transcript-links.json")
        )

        self.poll_interval = get_int("POLL_INTERVAL_SECONDS", 10)
        self.max_retries = get_int("MAX_RETRIES", 3)
        self.retry_backoff_base = get_float("RETRY_BACKOFF_BASE", 60.0)

        # Notification settings
        self.notify_on_success = get("NOTIFY_ON_SUCCESS", "true").lower() == "true"
        self.notify_on_failure = get("NOTIFY_ON_FAILURE", "true").lower() == "true"
        self.ntfy_topic = get("NTFY_TOPIC", "")
        self.ntfy_url = f"https://ntfy.sh/{self.ntfy_topic}" if self.ntfy_topic else ""

        self.supported_extensions = {".m4a", ".mp3", ".wav", ".mp4", ".aac"}

        # Ensure directories exist
        for d in (self.watch_dir, self.transcripts_dir, self.done_dir,
                  self.failed_dir, self.log_dir, self.tmp_dir, self.processing_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.log_file = self.log_dir / "runtime.log"
        self.error_file = self.log_dir / "error.log"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(config: DaemonConfig, message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line, flush=True)
    with config.log_file.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_error(config: DaemonConfig, message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line, file=sys.stderr, flush=True)
    with config.error_file.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# Status tracking (reuses status.py via subprocess for compatibility)
# ---------------------------------------------------------------------------

def update_status(config: DaemonConfig, *args: str) -> None:
    if not config.status_script.exists():
        return
    try:
        subprocess.run(
            [str(config.venv_python), str(config.status_script), str(config.status_file), *args],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def notify(config: DaemonConfig, title: str, message: str) -> None:
    """Send macOS notification + optional ntfy push."""
    # macOS native notification
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{message}" with title "{title}"'],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass

    # ntfy push (for phone notifications when away from Mac)
    if config.ntfy_url:
        try:
            subprocess.run(
                ["curl", "-s", "-d", f"{title}: {message}", config.ntfy_url],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# File processing
# ---------------------------------------------------------------------------

def is_supported_audio(path: Path, config: DaemonConfig) -> bool:
    return path.suffix.lower() in config.supported_extensions


def ingest_completed_transcript(
    config: Any,
    *,
    basename: str,
    original_name: str,
    done_audio: Path,
) -> Path | None:
    """Copy a cleaned transcript into Obsidian without re-running ASR."""
    if not config.obsidian_ingest_enabled:
        return None

    transcript_path = config.transcripts_dir / f"{basename}.txt"
    if not transcript_path.is_file():
        raise FileNotFoundError(f"Missing transcript output: {transcript_path}")

    rules: list[dict[str, Any]] = []
    if config.obsidian_links_file.is_file():
        payload = json.loads(config.obsidian_links_file.read_text(encoding="utf-8"))
        rules = payload.get("rules", payload) if isinstance(payload, dict) else payload
        if not isinstance(rules, list):
            raise ValueError("OBSIDIAN_LINKS_FILE must contain a list or an object with a rules list")

    from obsidian_ingest import ingest_transcript

    return ingest_transcript(
        transcript_path=transcript_path,
        output_dir=config.obsidian_transcripts_dir,
        original_name=original_name,
        source_audio=done_audio,
        vault_root=config.obsidian_vault_dir,
        rules=rules,
    )


def count_queue(config: DaemonConfig) -> int:
    try:
        return sum(1 for f in config.watch_dir.iterdir()
                   if f.is_file() and not f.name.startswith(".") and is_supported_audio(f, config))
    except OSError:
        return 0


def recover_processing_files(config: DaemonConfig) -> None:
    """On startup, move anything left in processing/ back to inbox."""
    if not config.processing_dir.exists():
        return
    moved = 0
    for f in config.processing_dir.iterdir():
        if f.is_file() and not f.name.startswith("."):
            dest = config.watch_dir / f.name
            shutil.move(str(f), str(dest))
            moved += 1
    if moved:
        log(config, f"Recovered {moved} file(s) from processing/ to inbox")


def process_single_file(config: DaemonConfig, file_path: Path) -> bool:
    """Process one file through transcribe_hviske.py. Returns True on success."""
    # Move to processing/ (atomic on same filesystem)
    processing_path = config.processing_dir / file_path.name
    try:
        shutil.move(str(file_path), str(processing_path))
    except OSError as exc:
        log_error(config, f"Failed to move {file_path.name} to processing/: {exc}")
        return False

    # Move back to inbox for the transcriber (it expects to move from watch_dir)
    # Actually, we pass --input directly, so the transcriber processes from any path.
    # The transcriber moves the file to done/ or failed/ itself.
    # But it moves from the input path, so we pass the processing path.
    # The transcriber's _safe_move will move it from processing/ to done/.

    update_status(config, "watcher", "--state", "processing",
                  "--files-in-queue", str(count_queue(config)))

    result = subprocess.run(
        [
            str(config.venv_python),
            str(config.transcribe_script),
            "--config", str(config.config_path),
            "--input", str(processing_path),
        ],
        capture_output=True,
        text=True,
        timeout=7200,  # 2 hour max per file
    )

    if result.returncode == 0:
        # The transcriber logs: "Finished transcription for <basename>.m4a; outputs: {...}"
        import re as _re
        basename_match = _re.search(
            r"Finished transcription for (.+?)\.(?:m4a|mp3|wav|mp4|aac)",
            result.stdout,
        )
        basename = basename_match.group(1) if basename_match else None

        # Post-process: clean ASR repetition loops from transcript files.
        clean_script = config.transcribe_script.parent / "clean_repetitions.py"
        if clean_script.exists() and basename:
            clean_result = subprocess.run(
                [str(config.venv_python), str(clean_script), basename,
                 "--transcripts-dir", str(config.transcripts_dir)],
                capture_output=True, text=True, timeout=30,
            )
            if clean_result.returncode == 0 and clean_result.stdout.strip():
                log(config, f"Cleaned repetitions: {clean_result.stdout.strip().replace(chr(10), ', ')}")

        obsidian_note: Path | None = None
        if basename:
            try:
                obsidian_note = ingest_completed_transcript(
                    config,
                    basename=basename,
                    original_name=file_path.name,
                    done_audio=config.done_dir / f"{basename}{file_path.suffix}",
                )
                if obsidian_note:
                    log(config, f"Saved transcript in Obsidian: {obsidian_note}")
            except Exception as exc:
                # ASR output remains safely available locally; do not rerun a
                # costly transcription because only the vault copy failed.
                log_error(config, f"Obsidian ingest failed for {file_path.name}: {exc}")

        log(config, f"Completed: {file_path.name}")
        if config.notify_on_success:
            notification_message = file_path.name
            if obsidian_note:
                notification_message += " — gemt i Obsidian"
            notify(config, "Transcription ready", notification_message)
        return True
    else:
        stderr = result.stderr.strip().split("\n")[-1] if result.stderr else "unknown error"
        log_error(config, f"Failed: {file_path.name}: {stderr}")
        return False


def process_with_retry(config: DaemonConfig, file_path: Path) -> bool:
    """Process a file with up to MAX_RETRIES attempts, exponential backoff."""
    for attempt in range(1, config.max_retries + 1):
        log(config, f"Processing (attempt {attempt}/{config.max_retries}): {file_path.name}")

        if process_single_file(config, file_path):
            return True

        if attempt < config.max_retries:
            backoff = config.retry_backoff_base * (2 ** (attempt - 1))
            log(config, f"Retrying in {backoff:.0f}s...")
            time.sleep(backoff)

            # The file may have been moved to failed/ by the transcriber.
            # Move it back to inbox for the next attempt.
            failed_path = config.failed_dir / file_path.name
            if failed_path.exists():
                shutil.move(str(failed_path), str(file_path))
            else:
                # File might still be in processing/ if the transcriber crashed
                processing_path = config.processing_dir / file_path.name
                if processing_path.exists():
                    shutil.move(str(processing_path), str(file_path))
                else:
                    log_error(config, f"File disappeared: {file_path.name}")
                    return False
        else:
            # Final failure - file should already be in failed/ from the transcriber
            # But if it's still in processing/, move it to failed/
            processing_path = config.processing_dir / file_path.name
            if processing_path.exists():
                failed_path = config.failed_dir / file_path.name
                shutil.move(str(processing_path), str(failed_path))
                log_error(config, f"Moved to failed/: {file_path.name}")

            if config.notify_on_failure:
                notify(config, "Transcription failed",
                       f"{file_path.name} failed after {config.max_retries} attempts")
            return False

    return False


# ---------------------------------------------------------------------------
# Main daemon loop
# ---------------------------------------------------------------------------

def scan_and_process(config: DaemonConfig) -> None:
    """One pass: scan inbox, process all supported audio files."""
    update_status(config, "watcher", "--state", "scanning",
                  "--files-in-queue", str(count_queue(config)))

    files = sorted(
        [f for f in config.watch_dir.iterdir()
         if f.is_file() and not f.name.startswith(".") and is_supported_audio(f, config)],
        key=lambda p: p.stat().st_mtime,
    )

    for file_path in files:
        process_with_retry(config, file_path)

    update_status(config, "watcher", "--state", "sleeping",
                  "--files-in-queue", str(count_queue(config)),
                  "--poll-interval", str(config.poll_interval))


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Memo transcription daemon")
    parser.add_argument("--config", required=True, help="Path to config.env")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit")
    args = parser.parse_args()

    config = DaemonConfig(Path(args.config).expanduser().resolve())

    # flock for single-instance enforcement (auto-released by kernel on death)
    lock_path = config.tmp_dir / ".daemon.lock"
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        print("Daemon already running. Exiting.", flush=True)
        return 0

    # SIGTERM handler for graceful launchd shutdown
    running = True

    def handle_sigterm(signum: int, frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, handle_sigterm)

    log(config, f"Daemon started (poll={config.poll_interval}s, retries={config.max_retries})")

    # Crash recovery
    recover_processing_files(config)

    update_status(config, "watcher", "--state", "started",
                  "--pid", str(os.getpid()),
                  "--poll-interval", str(config.poll_interval))

    while running:
        try:
            scan_and_process(config)
        except Exception as exc:
            log_error(config, f"Scan error: {exc}")

        if args.once:
            log(config, "Run-once mode complete.")
            break

        # Sleep in small increments so SIGTERM is responsive
        slept = 0
        while slept < config.poll_interval and running:
            time.sleep(1)
            slept += 1

    update_status(config, "watcher", "--state", "stopped")
    log(config, "Daemon stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
