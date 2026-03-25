# Local Memo Transcriber

Fully local macOS pipeline that automatically transcribes iPhone Voice Memos using a Hugging Face Whisper model. No cloud APIs, no subscriptions -- everything runs on your Mac.

```text
~/LocalMemoTranscriber/
├── config.env
├── transcribe_hviske.py
├── watch_and_transcribe.sh
├── install.sh
├── dashboard.py            (web dashboard)
├── status.py               (progress tracking)
├── MenuBarApp/              (native macOS menu bar app)
├── launchd/
├── inbox/                   (iCloud Drive watch folder)
├── transcripts/             (.txt, .json, .srt output)
├── done/                    (processed audio)
├── failed/
├── logs/
├── tmp/
├── status.json              (live pipeline state)
└── venv/
```

## How it works

1. Record a Voice Memo on iPhone and share it to **Files > iCloud Drive > LocalMemoTranscriber > inbox**.
2. The file syncs to the Mac via iCloud Drive.
3. A `launchd` agent polls the inbox every 30 minutes.
4. The watcher waits for file stability (handles iCloud sync delays and partially downloaded files).
5. `ffmpeg` normalizes the audio to mono 16 kHz PCM WAV.
6. Silence points are detected to split long audio at natural pauses instead of hard time cuts.
7. A local Whisper model transcribes each chunk. If Apple GPU (MPS) produces unusable output, it falls back to CPU automatically.
8. Outputs (`.txt`, `.json`, `.srt`) are written to `transcripts/`.
9. The original audio moves to `done/`, or `failed/` if anything breaks.

## Requirements

- macOS on Apple Silicon
- Python 3.11+ (3.11-3.13 preferred for PyTorch wheel availability)
- `ffmpeg` and `ffprobe` in PATH

```bash
brew install python@3.13 ffmpeg
```

## Install

```bash
git clone https://github.com/poppinpixels/local-memo-transcriber.git ~/LocalMemoTranscriber
cd ~/LocalMemoTranscriber
./install.sh
```

You can clone to any location -- the installer uses `~/LocalMemoTranscriber` as the default runtime directory regardless of where the repo lives. To use a custom runtime directory:

```bash
BASE_DIR_OVERRIDE=/path/to/runtime ./install.sh
```

The installer will:

- create the runtime folder structure
- create a venv and install Python dependencies
- copy `config.env.example` to `config.env` if missing
- render the `launchd` plist
- dry-run the config check
- load and start the launchd agent

If your default `python3` is not the one you want:

```bash
PYTHON_BIN_OVERRIDE=/opt/homebrew/bin/python3.13 ./install.sh
```

Other install toggles:

```bash
SKIP_LAUNCHD=1 ./install.sh                                          # skip launchd registration
SKIP_PIP_INSTALL=1 SKIP_LAUNCHD=1 ./install.sh                       # skip pip + launchd
BASE_DIR_OVERRIDE="$PWD/.test-runtime" SKIP_LAUNCHD=1 ./install.sh   # isolated test install
```

## Configure

Edit the runtime config after install:

```bash
nano ~/LocalMemoTranscriber/config.env
```

Key settings:

```env
MODEL_ID=openai/whisper-large-v3   # any AutoModelForSpeechSeq2Seq model
LANGUAGE=                          # ISO 639-1 code (en, da, de, ...) or empty for auto-detect
DEVICE_PREFERENCE=auto             # auto | cpu | mps
POLL_INTERVAL_SECONDS=1800
WATCH_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/LocalMemoTranscriber/inbox"
```

See `config.env.example` for the full list with explanations of every setting.

## Model

The default model is [`openai/whisper-large-v3`](https://huggingface.co/openai/whisper-large-v3), a multilingual Whisper model that supports 100+ languages. Change `MODEL_ID` in `config.env` to use any compatible `AutoModelForSpeechSeq2Seq` model. Some examples:

| Model | Use case |
|-------|----------|
| `openai/whisper-large-v3` | General multilingual (default) |
| `openai/whisper-large-v3-turbo` | Faster, slightly less accurate |
| `syvai/hviske-v3-conversation` | Danish conversational |

If the model is gated, authenticate with `huggingface-cli login` first.

The pipeline uses direct `model.generate(...)` calls rather than the generic Transformers ASR pipeline, which produced better results in local testing.

## Manual commands

Dry-run config and ffmpeg checks:

```bash
~/LocalMemoTranscriber/venv/bin/python ~/LocalMemoTranscriber/transcribe_hviske.py \
  --config ~/LocalMemoTranscriber/config.env \
  --dry-run
```

Run one polling pass:

```bash
~/LocalMemoTranscriber/watch_and_transcribe.sh \
  --config ~/LocalMemoTranscriber/config.env \
  --once
```

Process one file directly:

```bash
~/LocalMemoTranscriber/venv/bin/python ~/LocalMemoTranscriber/transcribe_hviske.py \
  --config ~/LocalMemoTranscriber/config.env \
  --input ~/LocalMemoTranscriber/inbox/example.m4a
```

## launchd

The installer renders a plist at:

```text
~/Library/LaunchAgents/local.memo-transcriber.plist
```

Useful commands:

```bash
launchctl print "gui/$(id -u)/local.memo-transcriber"
launchctl kickstart -k "gui/$(id -u)/local.memo-transcriber"
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/local.memo-transcriber.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/local.memo-transcriber.plist
```

### Full Disk Access (optional)

If the watcher can't read iCloud Drive files, you can compile and use the included launcher binary so macOS Full Disk Access can be granted to a dedicated binary rather than `/bin/bash`:

```bash
cc -o launchd/memo-transcriber-launcher launchd/launcher.c
```

Then grant Full Disk Access to `launchd/memo-transcriber-launcher` in System Settings > Privacy & Security.

## Output format

Input: `Ideer til workshop.m4a`

Output:

```text
2026-03-14_0935_ideer-til-workshop.m4a   (in done/)
2026-03-14_0935_ideer-til-workshop.txt   (in transcripts/)
2026-03-14_0935_ideer-til-workshop.json
2026-03-14_0935_ideer-til-workshop.srt
```

Basename conflicts get `-2`, `-3`, ... suffixes.

## Monitoring

Two GUI options are available to monitor the pipeline. Both read from `status.json`, which the watcher and transcriber update in real time.

### Menu bar app (native macOS)

A lightweight Swift app that lives in your menu bar. Shows a status icon that changes based on pipeline state, with a popover for progress, queue, history, and quick folder access.

Build and run:

```bash
cd MenuBarApp && ./build.sh && open build/Memo\ Transcriber.app
```

Requires Xcode Command Line Tools (`xcode-select --install`). The app targets macOS 14+ (Sonoma).

### Web dashboard

A browser-based dashboard with detailed stats, processing progress, system config, and live runtime logs.

```bash
~/LocalMemoTranscriber/venv/bin/python ~/LocalMemoTranscriber/dashboard.py \
  --config ~/LocalMemoTranscriber/config.env
```

Opens at `http://127.0.0.1:9888` by default. Use `--port` to change the port, or `--no-open` to skip opening the browser. Auto-refreshes every 2 seconds with zero external dependencies.

## Logs

```bash
tail -f ~/LocalMemoTranscriber/logs/runtime.log
tail -f ~/LocalMemoTranscriber/logs/error.log
```

## License

MIT License -- see [LICENSE](LICENSE).

This project invokes `ffmpeg` (GPL-2.0+) as a subprocess; it does not link against or bundle ffmpeg. All Python dependencies use permissive licenses (Apache-2.0 or BSD-3-Clause).

## Known limitations

- Subtitle timestamps are derived from chunk boundaries and generated text, not Whisper timestamp tokens, because timestamp generation degraded quality in local testing.
- MPS (Apple GPU) may produce unusable output with some models. The pipeline detects this per-chunk and falls back to CPU automatically. Set `DEVICE_PREFERENCE=cpu` to skip MPS entirely.
- If PyTorch wheels are unavailable for your Python version, install Python 3.13 and rerun with `PYTHON_BIN_OVERRIDE`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
