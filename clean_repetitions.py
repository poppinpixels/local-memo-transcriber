#!/usr/bin/env python3
"""Post-process transcription output to collapse ASR repetition loops.

hviske-v5.3 with greedy decoding gets stuck repeating tokens:
"4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 5"
"nej nej nej nej nej nej nej nej nej nej nej nej nej nej nej"

This module collapses:
1. Consecutive identical tokens (max 2 kept: "ja ja" is natural, "ja ja ja ja" is not)
2. Repeated n-grams (2-4 word sequences repeated 3+ times in a row)

Cleans .txt, .json (text + segments), and .srt files in place.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def collapse_repeated_tokens(text: str, max_consecutive: int = 2) -> str:
    """Collapse runs of identical tokens to at most max_consecutive."""
    tokens = text.split()
    if len(tokens) <= max_consecutive:
        return text

    result: list[str] = []
    for token in tokens:
        if len(result) >= max_consecutive and all(t == token for t in result[-max_consecutive:]):
            continue
        result.append(token)

    return " ".join(result)


def collapse_repeated_ngrams(text: str, max_n: int = 4, min_repeats: int = 3) -> str:
    """Collapse repeated n-gram sequences (n=2..4) repeated min_repeats+ times."""
    tokens = text.split()
    if len(tokens) < max_n * min_repeats:
        return text

    changed = True
    while changed:
        changed = False
        tokens = collapse_repeated_tokens(" ".join(tokens)).split()

        for n in range(min(max_n, len(tokens) // min_repeats), 1, -1):
            i = 0
            while i <= len(tokens) - n * min_repeats:
                ngram = tokens[i:i + n]
                repeats = 1
                j = i + n
                while j + n <= len(tokens) and tokens[j:j + n] == ngram:
                    repeats += 1
                    j += n

                if repeats >= min_repeats:
                    # Keep 1 instance, collapse the rest
                    tokens = tokens[:i + n] + tokens[i + n * repeats:]
                    changed = True
                    break
                else:
                    i += 1

            if changed:
                break

    return " ".join(tokens)


def clean_text(text: str) -> str:
    """Apply all cleaning steps to a text string."""
    if not text or not text.strip():
        return text

    # Step 1: collapse consecutive identical tokens
    text = collapse_repeated_tokens(text, max_consecutive=2)

    # Step 2: collapse repeated n-grams
    text = collapse_repeated_ngrams(text, max_n=4, min_repeats=3)

    # Step 3: clean up double spaces
    text = re.sub(r"\s+", " ", text).strip()

    return text


def clean_txt_file(path: Path) -> bool:
    """Clean a .txt transcript file in place. Returns True if changed."""
    original = path.read_text(encoding="utf-8")
    cleaned = clean_text(original)
    if cleaned != original:
        path.write_text(cleaned + "\n", encoding="utf-8")
        return True
    return False


def clean_json_file(path: Path) -> bool:
    """Clean a .json transcript file in place. Returns True if changed."""
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False

    if "text" in data:
        cleaned = clean_text(data["text"])
        if cleaned != data["text"]:
            data["text"] = cleaned
            changed = True

    for seg in data.get("segments", []):
        if "text" in seg:
            cleaned = clean_text(seg["text"])
            if cleaned != seg["text"]:
                seg["text"] = cleaned
                changed = True

    # Clean chunk_transcripts in raw_result
    raw = data.get("raw_result", {})
    for chunk in raw.get("chunk_transcripts", []):
        if "text" in chunk:
            cleaned = clean_text(chunk["text"])
            if cleaned != chunk["text"]:
                chunk["text"] = cleaned
                changed = True

    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return changed


def clean_srt_file(path: Path) -> bool:
    """Clean a .srt subtitle file in place. Returns True if changed."""
    original = path.read_text(encoding="utf-8")
    lines = original.split("\n")
    cleaned_lines: list[str] = []

    for line in lines:
        # SRT format: index, timestamp, text, blank
        if re.match(r"^\d+$", line.strip()) or "-->" in line or not line.strip():
            cleaned_lines.append(line)
        else:
            cleaned_lines.append(clean_text(line))

    cleaned = "\n".join(cleaned_lines)
    if cleaned != original:
        path.write_text(cleaned, encoding="utf-8")
        return True
    return False


def clean_transcript_files(txt_path: Path, json_path: Path, srt_path: Path) -> dict[str, bool]:
    """Clean all three output formats. Returns which were changed."""
    results = {}
    if txt_path.exists():
        results["txt"] = clean_txt_file(txt_path)
    if json_path.exists():
        results["json"] = clean_json_file(json_path)
    if srt_path.exists():
        results["srt"] = clean_srt_file(srt_path)
    return results


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: clean_repetitions.py <basename> [--transcripts-dir DIR]", file=sys.stderr)
        return 1

    basename = sys.argv[1]
    transcripts_dir = Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--transcripts-dir" else Path.home() / "LocalMemoTranscriber" / "transcripts"

    txt_path = transcripts_dir / f"{basename}.txt"
    json_path = transcripts_dir / f"{basename}.json"
    srt_path = transcripts_dir / f"{basename}.srt"

    results = clean_transcript_files(txt_path, json_path, srt_path)

    for fmt, changed in results.items():
        print(f"  {fmt}: {'cleaned' if changed else 'no changes'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
