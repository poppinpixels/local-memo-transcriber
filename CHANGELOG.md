# Changelog

All notable changes to this project are documented in this file.
This project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-06-25

### Added

- **daemon.py**: Python daemon replacing the bash watcher. Uses `flock` for single-instance enforcement (kernel-released on death, no stale locks). 10-second poll interval. `processing/` directory for crash-safe file lifecycle. 3x retry with exponential backoff. macOS notification on success and permanent failure. Crash recovery on startup (moves files from `processing/` back to `inbox`).
- **upload_server.py**: HTTP POST endpoint for receiving audio files from iPhone Shortcuts. Atomic delivery (write `.part`, fsync, `os.rename`) so the daemon only sees complete files. Serves status JSON at `/status` and a live HTML dashboard at `/`.
- **clean_repetitions.py**: Post-processing module that collapses ASR repetition loops caused by greedy decoding. Handles consecutive identical tokens (keeps natural doubles like "nej nej") and repeated n-grams. Cleans `.txt`, `.json`, and `.srt` files.
- **start_services.sh**: launchd-compatible launcher that starts both daemon and upload server.
- SemVer versioning.
- This changelog.

### Changed

- **README.md**: Fully rewritten to reflect the new architecture (daemon + upload server + cleaner).
- **config.env.example**: Updated with new settings (daemon, upload server, notifications, retry). Default `WATCH_DIR` now points to local inbox instead of iCloud Drive.
- Default `POLL_INTERVAL_SECONDS` changed from 1800 (30 min) to 10 seconds.
- Default `MODEL_ID` changed from `openai/whisper-large-v3` to `syvai/hviske-v5.3`.

### Removed

- **watch_and_transcribe.sh** is no longer the primary watcher (replaced by `daemon.py`). The file remains in the repo for reference but is not used by the launchd agent.
- iCloud Drive stub detection and stability-wait code paths are no longer needed (atomic HTTP delivery eliminates partial files). The transcriber engine still handles them if `WATCH_DIR` points to iCloud.

### Migration notes

If upgrading from a pre-1.0 install:

1. Update `config.env` with the new settings (see `config.env.example`)
2. Reload the launchd plist (it now runs `start_services.sh` instead of the bash watcher)
3. The transcription engine (`transcribe_hviske.py`) and status tracker (`status.py`) are unchanged

## [0.x] - Pre-release

Initial development with bash watcher, iCloud Drive inbox, 30-minute polling, and `mkdir`-based locking.
