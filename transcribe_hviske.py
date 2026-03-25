#!/usr/bin/env python3
"""Local transcription pipeline for iPhone Voice Memos on macOS."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

SUPPORTED_EXTENSIONS = {".m4a", ".mp3", ".wav", ".mp4", ".aac"}
DEFAULT_OUTPUT_FORMATS = ("txt", "json", "srt")


@dataclass
class Config:
    model_id: str
    watch_dir: Path
    transcripts_dir: Path
    done_dir: Path
    failed_dir: Path
    log_dir: Path
    tmp_dir: Path
    ffmpeg_bin: str
    ffprobe_bin: str
    language: str
    output_formats: tuple[str, ...]
    normalized_sample_rate: int
    normalized_channels: int
    chunk_length_seconds: int
    stride_length_seconds: int
    max_new_tokens: int
    device_preference: str
    silence_threshold_db: str
    min_silence_duration: float

    @property
    def runtime_log(self) -> Path:
        return self.log_dir / "runtime.log"

    @property
    def error_log(self) -> Path:
        return self.log_dir / "error.log"


@dataclass
class LoadedPipeline:
    model: Any
    processor: Any
    device_name: str
    dtype_name: str
    model_type: str


class PipelineError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transcribe one local memo file with a Hugging Face ASR model.")
    parser.add_argument("--config", required=True, help="Path to config.env")
    parser.add_argument("--input", help="Path to the audio file to process")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and optionally test ffmpeg normalization without loading the model",
    )
    return parser.parse_args()


def read_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value.startswith(('"', "'")) and value.endswith(('"', "'")) and len(value) >= 2:
            value = value[1:-1]
        data[key.strip()] = os.path.expandvars(value)
    return data


def load_config(config_path: Path) -> Config:
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    env = read_env_file(config_path)

    def get(name: str, default: str = "") -> str:
        return os.environ.get(name, env.get(name, default)).strip()

    def get_int(name: str, default: int) -> int:
        raw = get(name, str(default))
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"Invalid integer for {name}: {raw}") from exc

    def get_float(name: str, default: float) -> float:
        raw = get(name, str(default))
        try:
            return float(raw)
        except ValueError as exc:
            raise ValueError(f"Invalid float for {name}: {raw}") from exc

    def path_value(name: str, default: str) -> Path:
        return Path(get(name, default)).expanduser().resolve()

    output_formats = tuple(
        item.strip().lower()
        for item in get("OUTPUT_FORMATS", ",".join(DEFAULT_OUTPUT_FORMATS)).split(",")
        if item.strip()
    )

    return Config(
        model_id=get("MODEL_ID", "syvai/hviske-v3-conversation"),
        watch_dir=path_value("WATCH_DIR", str(Path.home() / "LocalMemoTranscriber" / "inbox")),
        transcripts_dir=path_value("TRANSCRIPTS_DIR", str(Path.home() / "LocalMemoTranscriber" / "transcripts")),
        done_dir=path_value("DONE_DIR", str(Path.home() / "LocalMemoTranscriber" / "done")),
        failed_dir=path_value("FAILED_DIR", str(Path.home() / "LocalMemoTranscriber" / "failed")),
        log_dir=path_value("LOG_DIR", str(Path.home() / "LocalMemoTranscriber" / "logs")),
        tmp_dir=path_value("TMP_DIR", str(Path.home() / "LocalMemoTranscriber" / "tmp")),
        ffmpeg_bin=get("FFMPEG_BIN", "ffmpeg"),
        ffprobe_bin=get("FFPROBE_BIN", "ffprobe"),
        language=get("LANGUAGE", "da"),
        output_formats=output_formats or DEFAULT_OUTPUT_FORMATS,
        normalized_sample_rate=get_int("NORMALIZED_SAMPLE_RATE", 16000),
        normalized_channels=get_int("NORMALIZED_CHANNELS", 1),
        chunk_length_seconds=get_int("CHUNK_LENGTH_SECONDS", 30),
        stride_length_seconds=get_int("STRIDE_LENGTH_SECONDS", 5),
        max_new_tokens=get_int("MAX_NEW_TOKENS", 448),
        device_preference=get("DEVICE_PREFERENCE", "auto").lower(),
        silence_threshold_db=get("SILENCE_THRESHOLD_DB", "-35dB"),
        min_silence_duration=get_float("MIN_SILENCE_DURATION", 0.3),
    )


def ensure_directories(config: Config) -> None:
    for directory in (
        config.watch_dir,
        config.transcripts_dir,
        config.done_dir,
        config.failed_dir,
        config.log_dir,
        config.tmp_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def log_info(config: Config, message: str) -> None:
    append_log(config.runtime_log, message)
    print(message)


def log_error(config: Config, message: str) -> None:
    append_log(config.error_log, message)
    print(message, file=sys.stderr)


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    collapsed = re.sub(r"[^a-z0-9]+", "-", lowered)
    collapsed = re.sub(r"-+", "-", collapsed).strip("-")
    return collapsed or "memo"


def resolve_timestamp(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return datetime.now()


def build_basename(source: Path) -> str:
    timestamp = resolve_timestamp(source)
    date_part = timestamp.strftime("%Y-%m-%d")
    time_part = timestamp.strftime("%H%M")
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}[_\s-]\d{4}[_\s-]", "", source.stem)
    slug = slugify(stem)
    return f"{date_part}_{time_part}_{slug}"


def basename_exists(candidate: str, config: Config, extension: str) -> bool:
    transcript_targets = [config.transcripts_dir / f"{candidate}.{fmt}" for fmt in DEFAULT_OUTPUT_FORMATS]
    audio_targets = [
        config.done_dir / f"{candidate}{extension}",
        config.failed_dir / f"{candidate}{extension}",
        config.tmp_dir / f"{candidate}{extension}",
    ]
    return any(target.exists() for target in transcript_targets + audio_targets)


def resolve_unique_basename(source: Path, config: Config) -> str:
    extension = source.suffix.lower()
    base = build_basename(source)
    candidate = base
    counter = 2
    while basename_exists(candidate, config, extension):
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def run_command(command: list[str], error_message: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"{error_message}: {stderr}") from exc


def normalize_audio(source: Path, destination: Path, config: Config) -> None:
    command = [
        config.ffmpeg_bin,
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        str(config.normalized_channels),
        "-ar",
        str(config.normalized_sample_rate),
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    run_command(command, "ffmpeg normalization failed")


def probe_duration_seconds(path: Path, config: Config) -> float:
    command = [
        config.ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = run_command(command, "ffprobe failed")
    raw = result.stdout.strip()
    return float(raw) if raw else 0.0


def detect_silence_points(audio_path: Path, config: Config) -> list[float]:
    """Find silence midpoints in audio using ffmpeg silencedetect."""
    command = [
        config.ffmpeg_bin,
        "-i",
        str(audio_path),
        "-af",
        f"silencedetect=noise={config.silence_threshold_db}:d={config.min_silence_duration}",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except Exception:
        return []
    output = result.stderr or ""
    silence_points: list[float] = []
    starts: list[float] = []
    for line in output.splitlines():
        start_match = re.search(r"silence_start:\s*([\d.]+)", line)
        if start_match:
            starts.append(float(start_match.group(1)))
            continue
        end_match = re.search(r"silence_end:\s*([\d.]+)", line)
        if end_match and starts:
            end = float(end_match.group(1))
            midpoint = (starts[-1] + end) / 2.0
            silence_points.append(midpoint)
    return silence_points


def resolve_safe_max_new_tokens(
    loaded: LoadedPipeline,
    requested_max_new_tokens: int,
    config: Config,
    *,
    no_timestamps: bool,
) -> int | None:
    if requested_max_new_tokens <= 0:
        return None

    model_config = getattr(loaded.model, "config", None)
    max_target_positions = getattr(model_config, "max_target_positions", None)
    if not isinstance(max_target_positions, int) or max_target_positions <= 0:
        return requested_max_new_tokens

    prompt_token_count = 1
    if hasattr(loaded.processor, "get_decoder_prompt_ids"):
        try:
            prompt_ids = loaded.processor.get_decoder_prompt_ids(
                task="transcribe",
                language=config.language or None,
                no_timestamps=no_timestamps,
            )
            if isinstance(prompt_ids, list):
                prompt_token_count += len(prompt_ids)
        except Exception:
            prompt_token_count = 1

    safe_max_new_tokens = max(1, max_target_positions - prompt_token_count)
    return min(requested_max_new_tokens, safe_max_new_tokens)


def load_pipeline(config: Config, force_device: str | None = None) -> LoadedPipeline:
    try:
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
    except ImportError as exc:
        raise PipelineError(
            "Missing Python dependencies. Run install.sh first to create the venv and install requirements."
        ) from exc

    candidates: list[tuple[str, Any]] = []
    preference = (force_device or config.device_preference).lower()

    if preference == "cpu":
        candidates = [("cpu", torch.float32)]
    elif preference == "mps":
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            candidates = [("mps", torch.float32), ("cpu", torch.float32)]
        else:
            candidates = [("cpu", torch.float32)]
    else:
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            candidates.append(("mps", torch.float32))
        candidates.append(("cpu", torch.float32))

    last_error: Exception | None = None

    for device_name, dtype in candidates:
        try:
            processor = AutoProcessor.from_pretrained(config.model_id)
            model = AutoModelForSpeechSeq2Seq.from_pretrained(
                config.model_id,
                dtype=dtype,
                low_cpu_mem_usage=True,
                use_safetensors=True,
            )
            model.to(device_name)
            model.eval()
            model_type = str(getattr(getattr(model, "config", None), "model_type", ""))
            return LoadedPipeline(
                model=model,
                processor=processor,
                device_name=device_name,
                dtype_name=str(dtype).replace("torch.", ""),
                model_type=model_type,
            )
        except Exception as exc:  # pragma: no cover - hardware/model dependent
            last_error = exc

    raise PipelineError(f"Could not load model {config.model_id}: {last_error}")


def load_normalized_waveform(audio_path: Path) -> tuple[np.ndarray, int]:
    try:
        with wave.open(str(audio_path), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
    except wave.Error as exc:
        raise PipelineError(f"Could not read normalized WAV audio: {exc}") from exc

    if channels != 1:
        raise PipelineError(f"Expected mono WAV after normalization, got {channels} channels")
    if sample_width != 2:
        raise PipelineError(f"Expected 16-bit PCM WAV after normalization, got sample width {sample_width}")

    waveform = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    return waveform, sample_rate


def split_text_for_subtitles(text: str, max_chars: int = 84) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []

    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", normalized) if part.strip()]
    parts: list[str] = []

    for sentence in sentences or [normalized]:
        if len(sentence) <= max_chars:
            parts.append(sentence)
            continue

        words = sentence.split()
        current: list[str] = []
        for word in words:
            candidate = " ".join(current + [word]).strip()
            if current and len(candidate) > max_chars:
                parts.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            parts.append(" ".join(current))

    return parts or [normalized]


def build_approximate_chunks(text: str, start_seconds: float, end_seconds: float) -> list[dict[str, Any]]:
    segments_text = split_text_for_subtitles(text)
    if not segments_text:
        return []

    total_span = max(0.0, end_seconds - start_seconds)
    if total_span <= 0:
        return [{"timestamp": [start_seconds, end_seconds], "text": text.strip()}]

    weights = [max(1, len(re.sub(r"\s+", "", segment_text))) for segment_text in segments_text]
    total_weight = sum(weights) or len(segments_text)

    segments: list[dict[str, Any]] = []
    cursor = start_seconds
    for index, segment_text in enumerate(segments_text):
        if index + 1 == len(segments_text):
            segment_end = end_seconds
        else:
            segment_end = cursor + (total_span * (weights[index] / total_weight))
        segment_end = max(cursor, min(segment_end, end_seconds))
        segments.append({"timestamp": [cursor, segment_end], "text": segment_text})
        cursor = segment_end

    return segments


def iterate_audio_chunks(total_samples: int, sample_rate: int, chunk_length_seconds: int) -> list[tuple[int, int]]:
    if total_samples <= 0:
        return []

    chunk_samples = max(sample_rate, int(chunk_length_seconds * sample_rate))
    if total_samples <= chunk_samples:
        return [(0, total_samples)]

    chunks: list[tuple[int, int]] = []
    start = 0
    while start < total_samples:
        end = min(total_samples, start + chunk_samples)
        chunks.append((start, end))
        start = end
    return chunks


def build_silence_aware_chunks(
    total_samples: int,
    sample_rate: int,
    chunk_length_seconds: int,
    stride_length_seconds: int,
    silence_points: list[float],
) -> list[tuple[int, int]]:
    """Build audio chunks that split at silence points near natural boundaries.

    Chunks are at most chunk_length_seconds long. The stride controls the search
    window: we look for a silence point in the last stride_length_seconds of each
    chunk. If none is found, we fall back to a hard cut at chunk_length_seconds.
    """
    if total_samples <= 0:
        return []

    max_chunk_samples = max(sample_rate, int(chunk_length_seconds * sample_rate))
    if total_samples <= max_chunk_samples:
        return [(0, total_samples)]

    search_window_samples = max(sample_rate, int(stride_length_seconds * sample_rate))
    min_chunk_samples = max(int(10 * sample_rate), max_chunk_samples - search_window_samples)
    silence_samples = sorted(int(point * sample_rate) for point in silence_points)

    chunks: list[tuple[int, int]] = []
    start = 0

    while start < total_samples:
        ideal_end = start + max_chunk_samples

        if ideal_end >= total_samples:
            chunks.append((start, total_samples))
            break

        min_end = start + min_chunk_samples
        candidates = [s for s in silence_samples if min_end <= s <= ideal_end and s > start]

        if candidates:
            best = min(candidates, key=lambda s: abs(s - ideal_end))
            chunks.append((start, best))
            start = best
        else:
            chunks.append((start, ideal_end))
            start = ideal_end

    return chunks


def is_usable_transcript(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if re.fullmatch(r"([^\w\s])\1{2,}", stripped):
        return False
    if not any(character.isalnum() for character in stripped):
        return False
    return True


def generate_whisper_chunk_text(
    loaded: LoadedPipeline,
    chunk_audio: np.ndarray,
    sample_rate: int,
    config: Config,
) -> str:
    try:
        import torch
    except ImportError as exc:
        raise PipelineError(
            "Missing Python dependencies. Run install.sh first to create the venv and install requirements."
        ) from exc

    inputs = loaded.processor.feature_extractor(chunk_audio, sampling_rate=sample_rate, return_tensors="pt")
    input_features = inputs.input_features.to(device=loaded.device_name, dtype=loaded.model.dtype)
    safe_max_new_tokens = resolve_safe_max_new_tokens(loaded, config.max_new_tokens, config, no_timestamps=True)

    attempts: list[dict[str, Any]] = [{"task": "transcribe"}]
    if config.language:
        attempts.insert(0, {"task": "transcribe", "language": config.language})

    best_text = ""
    last_error: Exception | None = None

    for attempt in attempts:
        generate_kwargs = dict(attempt)
        if safe_max_new_tokens is not None:
            generate_kwargs["max_new_tokens"] = safe_max_new_tokens

        try:
            with torch.no_grad():
                generated_ids = loaded.model.generate(input_features, **generate_kwargs)
            text = loaded.processor.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()
            if text and len(text) > len(best_text):
                best_text = text
            if is_usable_transcript(text):
                return text
        except Exception as exc:
            last_error = exc

    if best_text:
        return best_text

    if last_error is not None:
        raise PipelineError(f"Direct Whisper generation failed: {last_error}") from last_error

    raise PipelineError("Direct Whisper generation returned empty output")


def transcribe_audio(loaded: LoadedPipeline, audio_path: Path, config: Config, duration_seconds: float, *, silence_points: list[float] | None = None) -> dict[str, Any]:
    if loaded.model_type != "whisper":
        raise PipelineError(
            f"Model {config.model_id} is model_type={loaded.model_type or 'unknown'}, but this pipeline now uses direct Whisper generation."
        )

    waveform, sample_rate = load_normalized_waveform(audio_path)
    if waveform.size == 0:
        raise PipelineError("Normalized audio file is empty")

    if silence_points:
        chunk_ranges = build_silence_aware_chunks(
            len(waveform), sample_rate, config.chunk_length_seconds,
            config.stride_length_seconds, silence_points,
        )
    else:
        chunk_ranges = iterate_audio_chunks(len(waveform), sample_rate, config.chunk_length_seconds)

    transcript_parts: list[str] = []
    chunks: list[dict[str, Any]] = []
    chunk_summaries: list[dict[str, Any]] = []
    current_loaded = loaded

    for start_sample, end_sample in chunk_ranges:
        chunk_audio = waveform[start_sample:end_sample]
        chunk_start_seconds = start_sample / sample_rate
        chunk_end_seconds = end_sample / sample_rate
        chunk_text = generate_whisper_chunk_text(current_loaded, chunk_audio, sample_rate, config)

        if not is_usable_transcript(chunk_text) and current_loaded.device_name == "mps" and config.device_preference != "mps":
            log_info(config, f"MPS output unusable at {chunk_start_seconds:.1f}s; switching to CPU")
            current_loaded = load_pipeline(config, force_device="cpu")
            chunk_text = generate_whisper_chunk_text(current_loaded, chunk_audio, sample_rate, config)

        normalized_chunk_text = chunk_text.strip()
        if not normalized_chunk_text:
            continue

        transcript_parts.append(normalized_chunk_text)
        chunk_summaries.append(
            {
                "start": chunk_start_seconds,
                "end": chunk_end_seconds,
                "text": normalized_chunk_text,
            }
        )
        chunks.extend(build_approximate_chunks(normalized_chunk_text, chunk_start_seconds, chunk_end_seconds))

    transcript_text = " ".join(part for part in transcript_parts if part).strip()
    if not transcript_text and duration_seconds > 0:
        raise PipelineError("Transcription returned empty transcript")

    return {
        "text": transcript_text,
        "chunks": chunks,
        "engine": "whisper-generate",
        "model_type": current_loaded.model_type,
        "device_used": current_loaded.device_name,
        "dtype_used": current_loaded.dtype_name,
        "approximate_timestamps": True,
        "silence_aware_chunks": bool(silence_points),
        "chunk_length_seconds": config.chunk_length_seconds,
        "stride_length_seconds": config.stride_length_seconds,
        "chunk_count": len(chunk_summaries),
        "chunk_transcripts": chunk_summaries,
    }


def normalize_segments(chunks: Any, full_text: str, duration_seconds: float) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for chunk in chunks or []:
        if not isinstance(chunk, dict):
            continue
        text = str(chunk.get("text", "")).strip()
        timestamp = chunk.get("timestamp") or chunk.get("timestamps") or []
        start = None
        end = None
        if isinstance(timestamp, (list, tuple)):
            if len(timestamp) >= 1 and timestamp[0] is not None:
                start = float(timestamp[0])
            if len(timestamp) >= 2 and timestamp[1] is not None:
                end = float(timestamp[1])
        segments.append({"start": start, "end": end, "text": text})

    if not segments and full_text.strip():
        return [{"start": 0.0, "end": duration_seconds or 0.0, "text": full_text.strip()}]

    for index, segment in enumerate(segments):
        if segment["start"] is None:
            segment["start"] = segments[index - 1]["end"] if index > 0 else 0.0
        if segment["end"] is None:
            if index + 1 < len(segments) and segments[index + 1]["start"] is not None:
                segment["end"] = segments[index + 1]["start"]
            else:
                segment["end"] = duration_seconds or segment["start"]
        if segment["end"] < segment["start"]:
            segment["end"] = segment["start"]
    return segments


def format_srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render_srt(segments: list[dict[str, Any]]) -> str:
    entries: list[str] = []
    for index, segment in enumerate(segments, start=1):
        text = segment.get("text", "").strip()
        if not text:
            continue
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        entries.append(
            f"{index}\n{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n{text}\n"
        )
    return "\n".join(entries).strip() + ("\n" if entries else "")


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


def write_output_files(
    basename: str,
    source_original_name: str,
    final_audio_path: Path,
    transcript_text: str,
    result: dict[str, Any],
    segments: list[dict[str, Any]],
    duration_seconds: float,
    loaded: LoadedPipeline,
    config: Config,
) -> dict[str, Path]:
    outputs: dict[str, Path] = {}

    if "txt" in config.output_formats:
        txt_path = config.transcripts_dir / f"{basename}.txt"
        txt_path.write_text(transcript_text.strip() + "\n", encoding="utf-8")
        outputs["txt"] = txt_path

    if "json" in config.output_formats:
        json_path = config.transcripts_dir / f"{basename}.json"
        payload = {
            "basename": basename,
            "source_original_name": source_original_name,
            "source_audio": str(final_audio_path),
            "model_id": config.model_id,
            "language": config.language,
            "device": result.get("device_used", loaded.device_name),
            "dtype": result.get("dtype_used", loaded.dtype_name),
            "duration_seconds": duration_seconds,
            "normalized_sample_rate": config.normalized_sample_rate,
            "normalized_channels": config.normalized_channels,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "text": transcript_text,
            "segments": segments,
            "raw_result": json_safe(result),
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs["json"] = json_path

    if "srt" in config.output_formats:
        srt_path = config.transcripts_dir / f"{basename}.srt"
        srt_path.write_text(render_srt(segments), encoding="utf-8")
        outputs["srt"] = srt_path

    return outputs


def cleanup_output_files(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


def _is_locally_downloaded(path: Path) -> bool:
    """Check whether an iCloud Drive file is actually downloaded (not just a stub)."""
    try:
        st = path.stat()
        # iCloud stubs report a logical size but use 0 disk blocks.
        return st.st_blocks > 0 or st.st_size == 0
    except OSError:
        return False


def _safe_move(source: Path, destination: Path, *, retries: int = 3, retry_delay: float = 5.0) -> None:
    """Move a file via copy+delete (avoids iCloud rename/deadlock issues).

    Retries on EDEADLK (errno 11) which occurs when iCloud Drive holds a
    lock on a file that is being synced or has not been downloaded locally.
    Cleans up partial destination files on failure.
    """
    import errno as errno_mod
    import time

    if not _is_locally_downloaded(source):
        # Try to trigger iCloud download via subprocess; it may take a while.
        subprocess.run(["brctl", "download", str(source)], capture_output=True, timeout=10)
        # Give iCloud a moment, then re-check.
        time.sleep(2)
        if not _is_locally_downloaded(source):
            raise OSError(
                errno_mod.EAGAIN,
                f"File not yet downloaded from iCloud (0 disk blocks): {source}",
            )

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            shutil.copy2(str(source), str(destination))
            source.unlink()
            return
        except OSError as exc:
            last_exc = exc
            # Clean up any partial/empty destination left by the failed copy.
            if destination.exists() and destination.stat().st_size == 0:
                destination.unlink(missing_ok=True)
            if exc.errno == errno_mod.EDEADLK and attempt < retries:
                time.sleep(retry_delay * attempt)
                continue
            raise
    raise last_exc  # type: ignore[misc]  # unreachable, satisfies type-checker


def move_to_directory(source: Path, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    if not destination.exists():
        _safe_move(source, destination)
        return destination

    stem = source.stem
    suffix = source.suffix
    counter = 2
    while True:
        candidate = destination_dir / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            _safe_move(source, candidate)
            return candidate
        counter += 1


def validate_input_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Input path is not a file: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported audio format: {path.suffix}")


def run_dry_run(config: Config, input_path: Path | None) -> None:
    ensure_directories(config)
    shutil.which(config.ffmpeg_bin) or (_ for _ in ()).throw(FileNotFoundError(f"ffmpeg not found: {config.ffmpeg_bin}"))
    shutil.which(config.ffprobe_bin) or (_ for _ in ()).throw(FileNotFoundError(f"ffprobe not found: {config.ffprobe_bin}"))

    summary: dict[str, Any] = {
        "config": {
            "model_id": config.model_id,
            "watch_dir": str(config.watch_dir),
            "transcripts_dir": str(config.transcripts_dir),
            "done_dir": str(config.done_dir),
            "failed_dir": str(config.failed_dir),
            "log_dir": str(config.log_dir),
            "tmp_dir": str(config.tmp_dir),
            "device_preference": config.device_preference,
            "output_formats": list(config.output_formats),
        }
    }

    if input_path is not None:
        validate_input_file(input_path)
        basename = resolve_unique_basename(input_path, config)
        normalized = config.tmp_dir / f"{basename}.dry-run.wav"
        normalize_audio(input_path, normalized, config)
        duration = probe_duration_seconds(normalized, config)
        normalized.unlink(missing_ok=True)
        summary["input"] = str(input_path)
        summary["basename"] = basename
        summary["normalized_duration_seconds"] = duration

    print(json.dumps(summary, ensure_ascii=False, indent=2))


def process_file(input_path: Path, config: Config) -> int:
    ensure_directories(config)
    validate_input_file(input_path)

    shutil.which(config.ffmpeg_bin) or (_ for _ in ()).throw(FileNotFoundError(f"ffmpeg not found: {config.ffmpeg_bin}"))
    shutil.which(config.ffprobe_bin) or (_ for _ in ()).throw(FileNotFoundError(f"ffprobe not found: {config.ffprobe_bin}"))

    basename = resolve_unique_basename(input_path, config)
    source_original_name = input_path.name
    working_audio = config.tmp_dir / f"{basename}{input_path.suffix.lower()}"
    normalized_audio = config.tmp_dir / f"{basename}.normalized.wav"
    expected_done_audio = config.done_dir / working_audio.name
    failed_target: Path | None = None
    written_output_paths: list[Path] = []

    log_info(config, f"Starting transcription for {input_path}")

    try:
        _safe_move(input_path, working_audio)
        normalize_audio(working_audio, normalized_audio, config)
        duration_seconds = probe_duration_seconds(normalized_audio, config)
        silence_points = detect_silence_points(normalized_audio, config)
        log_info(config, f"Detected {len(silence_points)} silence points in {duration_seconds:.1f}s audio")

        loaded = load_pipeline(config)
        log_info(
            config,
            f"Loaded model {config.model_id} on device={loaded.device_name} dtype={loaded.dtype_name}; transcribing {working_audio.name}",
        )
        result = transcribe_audio(loaded, normalized_audio, config, duration_seconds, silence_points=silence_points)
        transcript_text = str(result.get("text", "")).strip()

        if not is_usable_transcript(transcript_text):
            raise PipelineError("Transcription returned unusable text after all attempts")

        segments = normalize_segments(result.get("chunks"), transcript_text, duration_seconds)
        outputs = write_output_files(
            basename=basename,
            source_original_name=source_original_name,
            final_audio_path=expected_done_audio,
            transcript_text=transcript_text,
            result=result,
            segments=segments,
            duration_seconds=duration_seconds,
            loaded=loaded,
            config=config,
        )
        written_output_paths = list(outputs.values())
        done_audio = move_to_directory(working_audio, config.done_dir)
        log_info(config, f"Finished transcription for {done_audio.name}; outputs: {json_safe(outputs)}")
        return 0
    except Exception as exc:
        cleanup_output_files(written_output_paths)
        # Only move to failed/ if the working copy has real content (not a
        # 0-byte stub left behind by a failed iCloud copy).
        if working_audio.exists() and working_audio.stat().st_size > 0:
            failed_target = move_to_directory(working_audio, config.failed_dir)
        elif working_audio.exists():
            working_audio.unlink(missing_ok=True)  # discard empty stub

        failure_note = f"Transcription failed for {input_path}: {exc}"
        if failed_target is not None:
            failure_note += f" | moved to {failed_target}"
        log_error(config, failure_note)
        return 1
    finally:
        normalized_audio.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    config = load_config(Path(args.config).expanduser().resolve())
    input_path = Path(args.input).expanduser().resolve() if args.input else None

    if args.dry_run:
        run_dry_run(config, input_path)
        return 0

    if input_path is None:
        raise ValueError("--input is required unless --dry-run is used")

    return process_file(input_path, config)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Fatal error: {error}", file=sys.stderr)
        raise SystemExit(1)
