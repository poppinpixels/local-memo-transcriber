# Local Memo Transcriber

Fully local macOS pipeline that transcribes audio recordings using a Hugging Face ASR model. No cloud APIs, no subscriptions - everything runs on your Mac.

## How it works

```
iPhone Voice Memo
  -> Shortcut posts file via HTTP (multipart upload)
  -> Upload server writes atomically to inbox/ (write .part, fsync, rename)
  -> Daemon detects file within 10 seconds
  -> ffmpeg normalises to mono 16kHz PCM WAV
  -> Silence-aware chunking splits at natural pauses
  -> hviske-v5.3 transcribes each chunk on Apple Silicon (MPS/bf16)
  -> Repetition cleaner collapses ASR loops
  -> Outputs .txt, .json, .srt to transcripts/
  -> Original audio moves to done/ (or failed/ on error)
  -> macOS notification fires on completion or permanent failure
```

## Architecture

```
~/LocalMemoTranscriber/              # runtime directory
├── config.env                       # all configuration
├── inbox/                           # audio files arrive here
├── transcripts/                     # .txt, .json, .srt output
├── done/                            # processed originals
├── failed/                          # failed originals (after 3 retries)
├── logs/                            # runtime.log, error.log, launchd logs
├── tmp/
│   ├── processing/                  # file being actively transcribed
│   └── .daemon.lock                 # flock (kernel-released on death)
├── status.json                      # live pipeline state
└── venv/                            # Python virtualenv

~/projects/local-memo-transcriber/   # code directory
├── daemon.py                        # watcher: 10s poll, flock, retry, notifications
├── upload_server.py                 # HTTP endpoint: POST /upload, GET /status
├── transcribe_hviske.py             # transcription engine (frozen - do not modify)
├── clean_repetitions.py             # post-processing: collapse ASR repetition loops
├── status.py                        # status tracking (frozen)
├── start_services.sh                # launchd launcher (starts daemon + server)
├── dashboard.py                     # standalone web dashboard (optional)
├── MenuBarApp/                      # native macOS menu bar app (optional)
├── launchd/
│   ├── memo-transcriber-launcher    # compiled C wrapper for Full Disk Access
│   └── memo-transcriber.plist       # launchd agent template
└── config.env.example               # annotated config reference
```

## Requirements

- macOS on Apple Silicon
- Python 3.11+ (3.13 preferred for PyTorch wheel availability)
- `ffmpeg` and `ffprobe` in PATH

```bash
brew install python@3.13 ffmpeg
```

## Install

```bash
git clone https://github.com/poppinpixels/local-memo-transcriber.git ~/projects/local-memo-transcriber
cd ~/projects/local-memo-transcriber
./install.sh
```

The installer will:
- create the runtime folder structure at `~/LocalMemoTranscriber/`
- create a venv and install Python dependencies
- copy `config.env.example` to `config.env` if missing
- render the launchd plist
- dry-run the config check
- load and start the launchd agent

## Configure

Edit the runtime config:

```bash
# ~/LocalMemoTranscriber/config.env
MODEL_ID=syvai/hviske-v5.3
MODEL_REVISION=3f61b9c42f9cde65ce36bb621b6e03a2d0b379f9
LANGUAGE=da
WATCH_DIR=$HOME/LocalMemoTranscriber/inbox
POLL_INTERVAL_SECONDS=10
MAX_RETRIES=3
UPLOAD_PORT=9889
NOTIFY_ON_SUCCESS=true
NOTIFY_ON_FAILURE=true
```

See `config.env.example` for the full list with explanations.

## File delivery

### iPhone Shortcut (recommended)

Create a Shortcut that posts the Voice Memo via HTTP:

1. **Select File** (audio)
2. **Get Contents of URL** -> POST to `http://<mac-ip>:9889/upload`, pass the file as multipart form data
3. **Show Notification** with the response

The upload server writes atomically (`.part` file, fsync, `os.rename`), so the daemon only ever sees complete files. No stability wait, no stub detection needed.

### Manual copy

```bash
cp recording.m4a ~/LocalMemoTranscriber/inbox/
```

The daemon polls every 10 seconds. The file will be picked up automatically.

### Off-LAN delivery

Install [Tailscale](https://tailscale.com) on both devices. Use the Mac's Tailscale IP in the Shortcut URL. Works from any network, encrypted, no port forwarding.

## Model

The default model is [`syvai/hviske-v5.3`](https://huggingface.co/syvai/hviske-v5.3), a Danish-optimised cohere_asr model. ~6x faster than Whisper on Apple Silicon with fewer hallucinations.

| Model | Architecture | Use case |
|-------|--------------|----------|
| `syvai/hviske-v5.3` | cohere_asr | Danish, fast, fewer hallucinations (default) |
| `openai/whisper-large-v3` | whisper | General multilingual |
| `openai/whisper-large-v3-turbo` | whisper | Faster, slightly less accurate |
| `syvai/hviske-v3-conversation` | whisper | Danish conversational (Whisper fine-tune) |

If the model is gated, authenticate with `huggingface-cli login` first.

### Device + dtype

On Apple Silicon (MPS):
- `hviske-v5.x` -> `bfloat16` (matches training dtype, halves memory)
- Whisper -> `float32` (historical bf16 op-coverage issues on MPS)

The pipeline detects unusable MPS output per-chunk and falls back to CPU automatically.

### `trust_remote_code` and `MODEL_REVISION`

Models like `syvai/hviske-v5.3` ship custom Python code. The pipeline passes `trust_remote_code=True` and pins `MODEL_REVISION` to a known-good commit SHA to prevent silent code updates.

## Post-processing

### Repetition cleaning

hviske-v5.3 uses greedy decoding (`do_sample=False, num_beams=1`) which can enter repetition loops during silence. The `clean_repetitions.py` script runs automatically after each transcription and collapses:

- Consecutive identical tokens: `4 4 4 4 4` -> `4 4` (keeps natural doubles like "nej nej")
- Repeated n-grams: `det er en det er en det er en` -> `det er en`

Cleans `.txt`, `.json` (including segments), and `.srt` files.

## Monitoring

### Web dashboard

The upload server includes a built-in status page at `http://<mac-ip>:9889/`. Shows watcher state, pipeline progress, queue, and history. Auto-refreshes every 5 seconds.

### JSON API

```bash
curl http://127.0.0.1:9889/status
```

### Menu bar app (optional)

```bash
cd MenuBarApp && ./build.sh && open build/Memo\ Transcriber.app
```

A lightweight Swift app that shows pipeline state in the menu bar. Requires Xcode Command Line Tools. Targets macOS 14+.

### Logs

```bash
tail -f ~/LocalMemoTranscriber/logs/runtime.log
tail -f ~/LocalMemoTranscriber/logs/error.log
```

## launchd

The launchd agent runs `start_services.sh`, which starts both the daemon and the upload server. If either dies, launchd restarts the whole thing.

```bash
# Status
launchctl print "gui/$(id -u)/local.memo-transcriber"

# Restart
launchctl kickstart -k "gui/$(id -u)/local.memo-transcriber"

# Reload plist
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/local.memo-transcriber.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/local.memo-transcriber.plist
```

### Full Disk Access (optional)

Only needed if watching an iCloud Drive folder. Compile and use the included launcher binary:

```bash
cc -o launchd/memo-transcriber-launcher launchd/launcher.c
```

Grant Full Disk Access to `launchd/memo-transcriber-launcher` in System Settings > Privacy and Security.

## Output format

Input: `Møde om praktikpladser.m4a`

Output:
```
2026-06-25_1337_mde-om-praktikpladser.m4a   (in done/)
2026-06-25_1337_mde-om-praktikpladser.txt   (in transcripts/)
2026-06-25_1337_mde-om-praktikpladser.json
2026-06-25_1337_mde-om-praktikpladser.srt
```

Basename conflicts get `-2`, `-3`, ... suffixes.

## Crash recovery

- **flock** for single-instance enforcement (auto-released by kernel on process death - no stale locks)
- **processing/ directory**: files being transcribed move here. On startup, anything left in `processing/` moves back to `inbox`
- **3x retry** with exponential backoff (60s, 120s, 300s). After 3 failures, file moves to `failed/`
- **launchd KeepAlive**: restarts services if either process dies

## Manual commands

Dry-run config and ffmpeg checks:

```bash
~/LocalMemoTranscriber/venv/bin/python ~/projects/local-memo-transcriber/transcribe_hviske.py \
  --config ~/LocalMemoTranscriber/config.env \
  --dry-run
```

Run one daemon scan:

```bash
~/LocalMemoTranscriber/venv/bin/python ~/projects/local-memo-transcriber/daemon.py \
  --config ~/LocalMemoTranscriber/config.env \
  --once
```

Process one file directly:

```bash
~/LocalMemoTranscriber/venv/bin/python ~/projects/local-memo-transcriber/transcribe_hviske.py \
  --config ~/LocalMemoTranscriber/config.env \
  --input ~/LocalMemoTranscriber/inbox/example.m4a
```

Clean repetitions from existing transcripts:

```bash
~/LocalMemoTranscriber/venv/bin/python ~/projects/local-memo-transcriber/clean_repetitions.py \
  <basename> --transcripts-dir ~/LocalMemoTranscriber/transcripts
```

## Known limitations

- Subtitle timestamps are approximate (derived from chunk boundaries, not model timestamp tokens)
- MPS (Apple GPU) may produce unusable output with some models - pipeline detects this and falls back to CPU
- Greedy decoding in cohere_asr can cause repetition loops during silence - cleaned post-hoc by `clean_repetitions.py`
- If PyTorch wheels are unavailable for your Python version, install Python 3.13 and rerun with `PYTHON_BIN_OVERRIDE`

## Versioning

This project uses [Semantic Versioning](https://semver.org/). See the [changelog](CHANGELOG.md) for release history.

## License

MIT License - see [LICENSE](LICENSE).

This project invokes `ffmpeg` (GPL-2.0+) as a subprocess; it does not link against or bundle ffmpeg. All Python dependencies use permissive licenses (Apache-2.0 or BSD-3-Clause).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
