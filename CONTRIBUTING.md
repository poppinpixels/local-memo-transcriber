# Contributing

Thanks for your interest in improving Local Memo Transcriber! This guide covers the basics.

## Getting started

1. Fork the repo and clone your fork.
2. Run the installer in test mode to avoid touching your real setup:

```bash
BASE_DIR_OVERRIDE="$PWD/.test-runtime" SKIP_LAUNCHD=1 ./install.sh
```

3. Run a dry-run to verify everything works:

```bash
.test-runtime/venv/bin/python transcribe_hviske.py \
  --config .test-runtime/config.env \
  --dry-run
```

## Making changes

- Open an issue first if the change is non-trivial (new feature, architecture change).
- Bug fixes and documentation improvements can go straight to a PR.
- Keep PRs focused on a single concern.

## Testing your changes

There is no automated test suite yet. Before submitting a PR, please verify:

1. `./install.sh` completes without errors (use `BASE_DIR_OVERRIDE` for isolation).
2. The dry-run passes.
3. If you changed transcription logic, test with at least one real audio file.

## Code style

- Python: follow the existing style in `transcribe_hviske.py` (type hints, dataclasses, no external linting config yet).
- Shell: `set -euo pipefail`, quote variables, use `shellcheck` if available.

## Commit messages

- Use a short, imperative subject line (e.g., "Fix MPS fallback for short audio chunks").
- Add detail in the body if the change is not obvious from the diff.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
