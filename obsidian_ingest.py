#!/usr/bin/env python3
"""Copy completed transcripts into Obsidian's raw source bank."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any


def _recorded_date(original_name: str) -> str:
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", original_name)
    return match.group(1) if match else "unknown"


def _display_title(original_name: str) -> str:
    stem = Path(original_name).stem
    return re.sub(r"^\d{4}-\d{2}-\d{2}[-_ ]*", "", stem).strip() or "Transskription"


def _matching_rules(
    original_name: str,
    transcript_text: str,
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    name = original_name.casefold()
    text = transcript_text.casefold()
    return [
        rule
        for rule in rules
        if (
            any(pattern.casefold() in name for pattern in rule.get("filename_patterns", []))
            or any(pattern.casefold() in text for pattern in rule.get("transcript_patterns", []))
        )
    ]


def _verified_link_titles(vault_root: Path, matched_rules: list[dict[str, Any]]) -> list[str]:
    titles: list[str] = []
    for rule in matched_rules:
        for relative_path in rule.get("links", []):
            target = (vault_root / relative_path).resolve()
            if target.is_relative_to(vault_root.resolve()) and target.suffix == ".md" and target.is_file():
                title = target.stem
                if title not in titles:
                    titles.append(title)
    return titles


def ingest_transcript(
    *,
    transcript_path: Path,
    output_dir: Path,
    original_name: str,
    source_audio: Path,
    vault_root: Path,
    rules: list[dict[str, Any]],
    now: datetime | None = None,
) -> Path:
    """Create one immutable Obsidian source note for a completed transcript."""
    ingested_at = now or datetime.now()
    transcript_text = transcript_path.read_text(encoding="utf-8").strip()
    title = _display_title(original_name)
    matched_rules = _matching_rules(original_name, transcript_text, rules)
    tags = ["transcription", "meeting"]
    for rule in matched_rules:
        for tag in rule.get("tags", []):
            if tag not in tags:
                tags.append(tag)
    links = _verified_link_titles(vault_root, matched_rules)

    output_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{ingested_at:%Y-%m-%d %H-%M} Morten Walsted - {title} - Transskription.md"
    destination = output_dir / file_name
    suffix = 2
    while destination.exists():
        destination = output_dir / f"{ingested_at:%Y-%m-%d %H-%M} Morten Walsted - {title} - Transskription-{suffix}.md"
        suffix += 1

    lines = [
        "---",
        "type: resource",
        f"created: {ingested_at:%Y-%m-%d}",
        f"recorded: {_recorded_date(original_name)}",
        'source: "audio memo"',
        "source-type: audio",
        f'source-audio: "{source_audio}"',
        f'local-transcript: "{transcript_path}"',
        f"routing: {', '.join(rule.get('id', 'unknown') for rule in matched_rules) or 'unclassified'}",
        f"links_status: {'linked' if links else 'needs-linking'}",
        f"tags: [{', '.join(tags)}]",
        "---",
        "",
        f"# {title}",
        "",
        "## Relateret materiale",
        "",
    ]
    if links:
        lines.extend(f"- [[{link}]]" for link in links)
    else:
        lines.append("- Ingen verificerede interne links endnu.")
    lines.extend(["", "## Transskription", "", transcript_text, ""])
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination
