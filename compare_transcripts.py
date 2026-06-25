#!/usr/bin/env python3
"""
compare_transcripts.py — Compare hviske-v3 vs hviske-v5.3 transcripts.

For every audio file that has both a v3 and a v5 transcript (matched by
duration), produces a per-pair report (length, structure, hallucination
loops, agreement, divergence regions, sample excerpts, capitalized-word
"entity" overlap) plus an aggregate summary. Stdlib only.

Usage:
    python3 compare_transcripts.py [--transcripts-dir DIR] [--out FILE]
                                   [--limit N] [--filter PATTERN]
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Loading & pair-matching
# ---------------------------------------------------------------------------

V3_TAG = "hviske-v3"
V5_TAG = "hviske-v5"


@dataclass
class Transcript:
    json_path: Path
    text: str
    model_id: str
    duration_seconds: float
    processing_seconds: float | None
    device: str | None
    dtype: str | None
    created_at: str | None
    chunk_count: int | None
    engine: str | None

    def kind(self) -> str | None:
        if V3_TAG in self.model_id:
            return "v3"
        if V5_TAG in self.model_id:
            return "v5"
        return None


def load_history_index(status_path: Path) -> dict[str, dict[str, Any]]:
    """Build basename -> latest history entry from status.json (keeps newest by completed_at)."""
    if not status_path.is_file():
        return {}
    try:
        d = json.load(open(status_path))
    except Exception:
        return {}
    history = d.get("history", []) or []
    index: dict[str, dict[str, Any]] = {}
    for h in history:
        base = h.get("basename")
        if not base:
            continue
        prev = index.get(base)
        if prev is None or (h.get("completed_at", "") > prev.get("completed_at", "")):
            index[base] = h
    return index


def load_transcript(json_path: Path, history_idx: dict[str, dict[str, Any]] | None = None) -> Transcript | None:
    try:
        d = json.load(open(json_path))
    except Exception:
        return None
    text = d.get("text", "")
    if not text:
        return None
    raw = d.get("raw_result") or {}

    processing = raw.get("processing_seconds")
    if processing is None and history_idx is not None:
        h = history_idx.get(json_path.stem)
        if h:
            processing = h.get("processing_seconds")

    return Transcript(
        json_path=json_path,
        text=text,
        model_id=d.get("model_id", ""),
        duration_seconds=float(d.get("duration_seconds", 0.0)),
        processing_seconds=processing,
        device=d.get("device") or raw.get("device_used"),
        dtype=d.get("dtype") or raw.get("dtype_used"),
        created_at=d.get("created_at"),
        chunk_count=raw.get("chunk_count"),
        engine=raw.get("engine"),
    )


def discover_pairs(
    transcripts_dir: Path,
    name_filter: str | None = None,
    history_idx: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[tuple[Transcript, Transcript]], list[tuple[str, str]]]:
    """Find (v3, v5) transcript pairs by matching duration_seconds.

    Returns (pairs, skipped) where skipped is a list of (reason, name) tuples
    suitable for the appendix.
    """
    all_t: list[Transcript] = []
    for j in sorted(transcripts_dir.glob("*.json")):
        if name_filter and name_filter not in j.name:
            continue
        t = load_transcript(j, history_idx=history_idx)
        if t is None:
            continue
        all_t.append(t)

    # Group by duration (rounded to 0.01s) — sub-second collisions across
    # unrelated recordings essentially never happen.
    groups: dict[float, list[Transcript]] = {}
    for t in all_t:
        groups.setdefault(round(t.duration_seconds, 2), []).append(t)

    pairs: list[tuple[Transcript, Transcript]] = []
    skipped: list[tuple[str, str]] = []
    for dur, members in groups.items():
        v3s = [m for m in members if m.kind() == "v3"]
        v5s = [m for m in members if m.kind() == "v5"]
        others = [m for m in members if m.kind() is None]
        for o in others:
            skipped.append(("unknown model_id (neither v3 nor v5)", o.json_path.name))
        if v3s and v5s:
            # Pick most recent of each
            v3 = max(v3s, key=lambda m: m.created_at or "")
            v5 = max(v5s, key=lambda m: m.created_at or "")
            pairs.append((v3, v5))
            # Note any extras within the same duration group
            for m in v3s:
                if m is not v3:
                    skipped.append(("extra v3 transcript (older v3 of paired audio)", m.json_path.name))
            for m in v5s:
                if m is not v5:
                    skipped.append(("extra v5.3 transcript (older v5 of paired audio)", m.json_path.name))
        elif v5s and not v3s:
            for m in v5s:
                skipped.append(("v5.3 only (no v3 transcript exists for this audio)", m.json_path.name))
        elif v3s and not v5s:
            for m in v3s:
                skipped.append(("v3 only (no v5.3 transcript exists for this audio)", m.json_path.name))
        else:
            skipped.append(("unpaired (unknown reason)", members[0].json_path.name))

    pairs.sort(key=lambda p: p[0].duration_seconds, reverse=True)
    return pairs, skipped


# ---------------------------------------------------------------------------
# Per-transcript metrics
# ---------------------------------------------------------------------------


SENTENCE_BOUNDARY = re.compile(r"[.!?]+")
WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)


def length_stats(text: str) -> dict[str, Any]:
    chars = len(text)
    words = text.split()
    sentences_raw = SENTENCE_BOUNDARY.split(text)
    sentences = [s.strip() for s in sentences_raw if s.strip()]
    sent_lens = [len(s.split()) for s in sentences] or [0]
    sent_lens_sorted = sorted(sent_lens)
    n = len(sent_lens_sorted)
    median = sent_lens_sorted[n // 2]
    p90 = sent_lens_sorted[min(int(n * 0.9), n - 1)] if n else 0
    unique = {w.lower() for w in WORD_RE.findall(text)}
    return {
        "chars": chars,
        "words": len(words),
        "sentences": len(sentences),
        "sent_median": median,
        "sent_p90": p90,
        "sent_max": max(sent_lens) if sent_lens else 0,
        "questions": text.count("?"),
        "exclamations": text.count("!"),
        "periods": len(re.findall(r"(?<![?!.])\.", text)),
        "unique_words": len(unique),
        "diversity": (len(unique) / len(words)) if words else 0.0,
    }


LOOP_RE = re.compile(
    r"\b(\w+)\b(?:[ ,.!?]+\1\b){7,}",  # ≥8 consecutive repeats of same word
    re.IGNORECASE,
)


def hallucination_loops(text: str) -> dict[str, Any]:
    loops = []
    for m in LOOP_RE.finditer(text):
        word = m.group(1).lower()
        span = m.group(0)
        count = len(re.findall(r"\b" + re.escape(word) + r"\b", span, re.IGNORECASE))
        loops.append({"word": word, "count": count, "span_chars": len(span), "start": m.start()})
    loops.sort(key=lambda l: -l["count"])
    return {
        "count": len(loops),
        "total_span": sum(l["span_chars"] for l in loops),
        "top": loops[:5],
    }


def extract_entities(text: str) -> set[str]:
    """Heuristic 'named entities' = capitalized tokens that are not sentence-initial.

    Not actual NER — surfaces proper-noun-spelling differences between models.
    Includes false positives (e.g. quoted or stylistic caps); filter caller-side
    if needed.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out: set[str] = set()
    for s in sentences:
        toks = s.split()
        for i, tok in enumerate(toks):
            if i == 0:
                continue
            clean = re.sub(r"[^\w]", "", tok)
            if not clean or len(clean) < 2:
                continue
            if clean[0].isupper() and any(c.islower() for c in clean):
                out.add(clean)
    return out


# ---------------------------------------------------------------------------
# Pair-level comparisons
# ---------------------------------------------------------------------------


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def shared_ngrams(tokens_a: list[str], tokens_b: list[str], n: int = 3) -> int:
    grams_a = {tuple(tokens_a[i:i + n]) for i in range(len(tokens_a) - n + 1)}
    grams_b = {tuple(tokens_b[i:i + n]) for i in range(len(tokens_b) - n + 1)}
    return len(grams_a & grams_b)


def diff_metrics(v3_text: str, v5_text: str) -> dict[str, Any]:
    v3_toks = v3_text.split()
    v5_toks = v5_text.split()
    sm = difflib.SequenceMatcher(None, v3_toks, v5_toks, autojunk=False)
    ratio = sm.ratio()  # 0..1 token-level similarity
    opcodes = sm.get_opcodes()

    # Top divergence regions by combined span
    divergences = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            continue
        span = max(i2 - i1, j2 - j1)
        if span < 4:  # skip tiny single-word substitutions
            continue
        divergences.append({
            "tag": tag,
            "v3_range": (i1, i2),
            "v5_range": (j1, j2),
            "v3_text": " ".join(v3_toks[i1:i2]),
            "v5_text": " ".join(v5_toks[j1:j2]),
            "span": span,
        })
    divergences.sort(key=lambda d: -d["span"])

    v3_words = {w.lower() for w in WORD_RE.findall(v3_text)}
    v5_words = {w.lower() for w in WORD_RE.findall(v5_text)}

    return {
        "ratio": ratio,
        "jaccard": jaccard(v3_words, v5_words),
        "shared_3grams": shared_ngrams(v3_toks, v5_toks, 3),
        "v3_unique_words": len(v3_words - v5_words),
        "v5_unique_words": len(v5_words - v3_words),
        "top_divergences": divergences[:3],
    }


def sample_excerpts(text: str, window: int = 300) -> dict[str, str]:
    if not text:
        return {"opening": "", "middle": "", "ending": ""}
    n = len(text)
    return {
        "opening": text[:window],
        "middle": text[max(0, n // 2 - window // 2):min(n, n // 2 + window // 2)],
        "ending": text[max(0, n - window):],
    }


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def fmt_dur(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return "?"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def pct_change(old: float, new: float) -> str:
    if old == 0:
        return "—"
    return f"{(new - old) / old * 100:+.1f}%"


def md_table(header: list[str], rows: list[list[str]]) -> str:
    out = "| " + " | ".join(header) + " |\n"
    out += "|" + "|".join(["---"] * len(header)) + "|\n"
    for row in rows:
        out += "| " + " | ".join(row) + " |\n"
    return out


def render_excerpts(label: str, exc: dict[str, str]) -> str:
    parts = []
    for win in ("opening", "middle", "ending"):
        snippet = exc[win].replace("\n", " ").strip()
        parts.append(f"**{label} — {win}**:\n\n> {snippet}\n")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Per-pair report
# ---------------------------------------------------------------------------


def per_pair_report(v3: Transcript, v5: Transcript, idx: int) -> tuple[str, dict[str, Any]]:
    basename = v5.json_path.stem  # v5 names tend to be canonical/date-prefixed
    v3_stats = length_stats(v3.text)
    v5_stats = length_stats(v5.text)
    v3_loops = hallucination_loops(v3.text)
    v5_loops = hallucination_loops(v5.text)
    diff = diff_metrics(v3.text, v5.text)
    v3_ents = extract_entities(v3.text)
    v5_ents = extract_entities(v5.text)
    v3_excerpts = sample_excerpts(v3.text)
    v5_excerpts = sample_excerpts(v5.text)

    summary_metrics = {
        "duration_seconds": v3.duration_seconds,
        "v3_chars": v3_stats["chars"],
        "v5_chars": v5_stats["chars"],
        "v3_words": v3_stats["words"],
        "v5_words": v5_stats["words"],
        "v3_unique": v3_stats["unique_words"],
        "v5_unique": v5_stats["unique_words"],
        "v3_diversity": v3_stats["diversity"],
        "v5_diversity": v5_stats["diversity"],
        "v3_sent_p90": v3_stats["sent_p90"],
        "v5_sent_p90": v5_stats["sent_p90"],
        "v3_loops": v3_loops["count"],
        "v5_loops": v5_loops["count"],
        "v3_loop_span": v3_loops["total_span"],
        "v5_loop_span": v5_loops["total_span"],
        "v3_max_loop": v3_loops["top"][0]["count"] if v3_loops["top"] else 0,
        "v5_max_loop": v5_loops["top"][0]["count"] if v5_loops["top"] else 0,
        "v3_proc": v3.processing_seconds,
        "v5_proc": v5.processing_seconds,
        "ratio": diff["ratio"],
        "jaccard": diff["jaccard"],
    }

    sections: list[str] = []
    sections.append(f"### Pair {idx}: `{basename}`")
    sections.append(f"Audio duration: **{fmt_dur(v3.duration_seconds)}** ({v3.duration_seconds:.1f}s)\n")

    # B. Metadata
    sections.append("#### Run metadata\n")
    sections.append(md_table(
        ["", "v3", "v5.3"],
        [
            ["model_id", v3.model_id, v5.model_id],
            ["created_at", v3.created_at or "?", v5.created_at or "?"],
            ["processing", fmt_dur(v3.processing_seconds), fmt_dur(v5.processing_seconds)],
            ["realtime ratio",
                f"{v3.duration_seconds/v3.processing_seconds:.1f}×" if v3.processing_seconds else "?",
                f"{v5.duration_seconds/v5.processing_seconds:.1f}×" if v5.processing_seconds else "?"],
            ["device / dtype",
                f"{v3.device or '?'} / {v3.dtype or '?'}",
                f"{v5.device or '?'} / {v5.dtype or '?'}"],
            ["engine / chunks",
                f"{v3.engine or '?'} / {v3.chunk_count or '?'}",
                f"{v5.engine or '?'} / {v5.chunk_count or '?'}"],
        ],
    ))

    # C. Length & structure
    sections.append("\n#### Length & structure\n")
    sections.append(md_table(
        ["metric", "v3", "v5.3", "Δ", "%"],
        [
            ["characters", f"{v3_stats['chars']:,}", f"{v5_stats['chars']:,}",
             f"{v5_stats['chars'] - v3_stats['chars']:+,}", pct_change(v3_stats['chars'], v5_stats['chars'])],
            ["words", f"{v3_stats['words']:,}", f"{v5_stats['words']:,}",
             f"{v5_stats['words'] - v3_stats['words']:+,}", pct_change(v3_stats['words'], v5_stats['words'])],
            ["sentences", f"{v3_stats['sentences']:,}", f"{v5_stats['sentences']:,}",
             f"{v5_stats['sentences'] - v3_stats['sentences']:+,}", pct_change(v3_stats['sentences'], v5_stats['sentences'])],
            ["sent_len median (words)", str(v3_stats['sent_median']), str(v5_stats['sent_median']), "—", "—"],
            ["sent_len p90", str(v3_stats['sent_p90']), str(v5_stats['sent_p90']),
             f"{v5_stats['sent_p90'] - v3_stats['sent_p90']:+d}", "—"],
            ["sent_len max", str(v3_stats['sent_max']), str(v5_stats['sent_max']),
             f"{v5_stats['sent_max'] - v3_stats['sent_max']:+d}", "—"],
            ["?/1k chars", f"{v3_stats['questions']*1000/max(v3_stats['chars'],1):.2f}",
             f"{v5_stats['questions']*1000/max(v5_stats['chars'],1):.2f}", "—", "—"],
            ["./1k chars", f"{v3_stats['periods']*1000/max(v3_stats['chars'],1):.2f}",
             f"{v5_stats['periods']*1000/max(v5_stats['chars'],1):.2f}", "—", "—"],
            ["unique words", f"{v3_stats['unique_words']:,}", f"{v5_stats['unique_words']:,}",
             f"{v5_stats['unique_words'] - v3_stats['unique_words']:+,}", "—"],
            ["lexical diversity", f"{v3_stats['diversity']:.3f}", f"{v5_stats['diversity']:.3f}",
             f"{v5_stats['diversity'] - v3_stats['diversity']:+.3f}", "—"],
        ],
    ))

    # D. Hallucination signals
    v3_max = v3_loops["top"][0]["count"] if v3_loops["top"] else 0
    v5_max = v5_loops["top"][0]["count"] if v5_loops["top"] else 0
    sections.append("\n#### Hallucination loops (≥8 same-word repeats)\n")
    sections.append(md_table(
        ["", "loops", "chars wasted", "% of transcript", "max loop size", "top loop"],
        [
            ["v3", str(v3_loops["count"]), f"{v3_loops['total_span']:,}",
             f"{v3_loops['total_span']*100/max(v3_stats['chars'],1):.2f}%",
             str(v3_max),
             (f"{v3_loops['top'][0]['count']}× '{v3_loops['top'][0]['word']}'" if v3_loops['top'] else "—")],
            ["v5.3", str(v5_loops["count"]), f"{v5_loops['total_span']:,}",
             f"{v5_loops['total_span']*100/max(v5_stats['chars'],1):.2f}%",
             str(v5_max),
             (f"{v5_loops['top'][0]['count']}× '{v5_loops['top'][0]['word']}'" if v5_loops['top'] else "—")],
        ],
    ))

    # E. Inter-model agreement
    sections.append("\n#### Inter-model agreement\n")
    sections.append(md_table(
        ["metric", "value"],
        [
            ["SequenceMatcher token ratio", f"{diff['ratio']:.4f}"],
            ["Word-set Jaccard", f"{diff['jaccard']:.4f}"],
            ["Shared 3-grams", f"{diff['shared_3grams']:,}"],
            ["Words only in v3", f"{diff['v3_unique_words']:,}"],
            ["Words only in v5.3", f"{diff['v5_unique_words']:,}"],
        ],
    ))

    # E.2. Top divergence regions
    if diff["top_divergences"]:
        sections.append("\n##### Top 3 divergence regions\n")
        for k, dvg in enumerate(diff["top_divergences"], 1):
            sections.append(f"**#{k}** ({dvg['tag']}, span={dvg['span']} tokens)\n")
            sections.append(f"> **v3** ({dvg['v3_range'][1]-dvg['v3_range'][0]} tok): {dvg['v3_text'][:400]}{'…' if len(dvg['v3_text'])>400 else ''}\n")
            sections.append(f"> **v5.3** ({dvg['v5_range'][1]-dvg['v5_range'][0]} tok): {dvg['v5_text'][:400]}{'…' if len(dvg['v5_text'])>400 else ''}\n")

    # F. Sample excerpts
    sections.append("\n#### Sample excerpts\n")
    sections.append(render_excerpts("v3", v3_excerpts))
    sections.append("")
    sections.append(render_excerpts("v5.3", v5_excerpts))

    # G. Entity proxy
    both = v3_ents & v5_ents
    only_v3 = v3_ents - v5_ents
    only_v5 = v5_ents - v3_ents
    sections.append("\n#### Entity-spelling overlap\n")
    sections.append("> Heuristic: capitalized tokens not at sentence start. Includes false positives — interpret directionally, not as gospel.\n")
    sections.append(md_table(
        ["", "count", "sample (first 12)"],
        [
            ["both", str(len(both)), ", ".join(sorted(both)[:12])],
            ["only v3", str(len(only_v3)), ", ".join(sorted(only_v3)[:12])],
            ["only v5.3", str(len(only_v5)), ", ".join(sorted(only_v5)[:12])],
        ],
    ))

    sections.append("\n---\n")
    return "\n".join(sections), summary_metrics


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def render_aggregate(pairs_summary: list[dict[str, Any]]) -> str:
    n = len(pairs_summary)
    if n == 0:
        return "## Aggregate\n\n_No pairs to aggregate._\n"

    def avg(key: str) -> float:
        vals = [p[key] for p in pairs_summary if p.get(key) is not None]
        return sum(vals) / len(vals) if vals else 0.0

    def wins(key_v3: str, key_v5: str, better: str) -> int:
        """Count files where v5 'wins' on this metric.  better='less' or 'more'."""
        c = 0
        for p in pairs_summary:
            v3v, v5v = p.get(key_v3), p.get(key_v5)
            if v3v is None or v5v is None:
                continue
            if better == "less" and v5v < v3v:
                c += 1
            elif better == "more" and v5v > v3v:
                c += 1
        return c

    out = ["## Aggregate\n"]
    out.append(f"_{n} pair(s) compared. Metrics weighted equally per file regardless of audio length._\n")

    # Speed headline
    v3_realtimes = [p["duration_seconds"] / p["v3_proc"] for p in pairs_summary if p.get("v3_proc")]
    v5_realtimes = [p["duration_seconds"] / p["v5_proc"] for p in pairs_summary if p.get("v5_proc")]
    v3_rt = sum(v3_realtimes) / len(v3_realtimes) if v3_realtimes else None
    v5_rt = sum(v5_realtimes) / len(v5_realtimes) if v5_realtimes else None
    out.append("### Speed (realtime ratio = audio_duration / processing_duration)\n")
    out.append(md_table(
        ["", "v3", "v5.3", "v5.3 / v3"],
        [["average realtime ratio",
          f"{v3_rt:.1f}× (n={len(v3_realtimes)})" if v3_rt is not None else "n/a",
          f"{v5_rt:.1f}× (n={len(v5_realtimes)})" if v5_rt is not None else "n/a",
          f"{v5_rt/v3_rt:.1f}×" if (v3_rt and v5_rt) else "—"]],
    ))
    out.append(
        f"\n_Only files whose processing time is still in `status.json` history are counted "
        f"(v3 n={len(v3_realtimes)}/{n}, v5.3 n={len(v5_realtimes)}/{n}). "
        f"Older March-era v3 runs have been rotated out of history._\n"
    )

    # Win rates
    out.append("\n### Win rates (per metric, files-where-v5.3-wins / total)\n")
    win_rows = [
        ["fewer hallucination loops", f"{wins('v3_loops','v5_loops','less')}/{n}"],
        ["less hallucination char-waste", f"{wins('v3_loop_span','v5_loop_span','less')}/{n}"],
        ["smaller max loop size", f"{wins('v3_max_loop','v5_max_loop','less')}/{n}"],
        ["tighter sentence p90 (lower)", f"{wins('v3_sent_p90','v5_sent_p90','less')}/{n}"],
        ["higher lexical diversity", f"{wins('v3_diversity','v5_diversity','more')}/{n}"],
        ["more unique words", f"{wins('v3_unique','v5_unique','more')}/{n}"],
        ["faster processing", f"{wins('v5_proc','v3_proc','more')}/{n}"],
        # ↑ "v5 wins faster" = v5 processing TIME is LESS than v3's = v3 > v5.
        # We use 'more' with reversed key order to express that.
    ]
    out.append(md_table(["metric", "v5.3 wins"], win_rows))

    # Averages of selected metrics
    avg_v3_loops = avg("v3_loops")
    avg_v5_loops = avg("v5_loops")
    avg_v3_waste_pct = sum(p["v3_loop_span"] * 100 / max(p["v3_chars"], 1) for p in pairs_summary) / n
    avg_v5_waste_pct = sum(p["v5_loop_span"] * 100 / max(p["v5_chars"], 1) for p in pairs_summary) / n
    avg_words_pct = sum(pct_to_float(pct_change(p["v3_words"], p["v5_words"])) for p in pairs_summary) / n
    avg_diversity_delta = sum(p["v5_diversity"] - p["v3_diversity"] for p in pairs_summary) / n
    avg_ratio = avg("ratio")
    avg_jaccard = avg("jaccard")

    out.append("\n### Averages across all pairs\n")
    out.append(md_table(
        ["metric", "v3", "v5.3", "Δ"],
        [
            ["hallucination loops per file", f"{avg_v3_loops:.1f}", f"{avg_v5_loops:.1f}",
             f"{avg_v5_loops - avg_v3_loops:+.1f}"],
            ["hallucination % of transcript", f"{avg_v3_waste_pct:.2f}%", f"{avg_v5_waste_pct:.2f}%",
             f"{avg_v5_waste_pct - avg_v3_waste_pct:+.2f}%"],
            ["avg word-count change v5 vs v3", "—", f"{avg_words_pct:+.1f}%", "—"],
            ["avg lexical diversity delta", "—", "—", f"{avg_diversity_delta:+.3f}"],
            ["avg SequenceMatcher ratio", "—", "—", f"{avg_ratio:.3f}"],
            ["avg word-set Jaccard", "—", "—", f"{avg_jaccard:.3f}"],
        ],
    ))

    # Vocabulary diversity note (advisor #5)
    diversity_wins = wins("v3_diversity", "v5_diversity", "more")
    shorter = sum(1 for p in pairs_summary if p["v5_words"] < p["v3_words"])
    out.append(
        f"\n_Vocabulary signal: v5.3 has higher lexical diversity on {diversity_wins}/{n} pairs; "
        f"v5.3 is shorter (fewer words) on {shorter}/{n}. Shorter + same-or-higher diversity → tighter prose; "
        f"shorter + lower diversity → may be dropping content._\n"
    )

    # Top 3 by edit distance
    sorted_by_div = sorted(pairs_summary, key=lambda p: p["ratio"])  # lowest ratio = most divergent
    out.append("\n### Pairs where the models disagree most (flag for human review)\n")
    out.append(
        "_A low token ratio means the two transcripts share little token order. It does **not** mean "
        "either model is right or wrong — only that they wrote different things for the same audio. "
        "Could be hallucinated content in one (or both), dropped sections, or genuinely different "
        "interpretations of unclear speech. Worth listening back to verify._\n"
    )
    rows = []
    for p in sorted_by_div[:3]:
        rows.append([p["basename"], f"{p['ratio']:.3f}", f"{p['jaccard']:.3f}",
                     fmt_dur(p["duration_seconds"])])
    out.append(md_table(["basename", "token ratio", "Jaccard", "duration"], rows))

    return "\n".join(out) + "\n"


def pct_to_float(pct_str: str) -> float:
    """Parse '+12.3%' or '—' to a float (0.0 for '—')."""
    if not pct_str or pct_str == "—":
        return 0.0
    s = pct_str.replace("%", "").replace("+", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--transcripts-dir",
                        default=str(Path.home() / "LocalMemoTranscriber" / "transcripts"),
                        help="Directory with .json transcript files")
    parser.add_argument("--out",
                        default=str(Path.home() / "LocalMemoTranscriber" / "COMPARISON_REPORT.md"),
                        help="Output markdown path")
    parser.add_argument("--limit", type=int, default=0,
                        help="Process at most N pairs (0 = all)")
    parser.add_argument("--filter", default=None,
                        help="Substring filter on transcript filenames")
    args = parser.parse_args()

    transcripts_dir = Path(args.transcripts_dir).expanduser().resolve()
    if not transcripts_dir.is_dir():
        print(f"transcripts dir not found: {transcripts_dir}", file=sys.stderr)
        return 1

    print(f"Scanning {transcripts_dir} ...", file=sys.stderr)
    status_path = transcripts_dir.parent / "status.json"
    history_idx = load_history_index(status_path)
    if history_idx:
        print(f"  loaded {len(history_idx)} history entries from {status_path.name}", file=sys.stderr)
    pairs, skipped = discover_pairs(transcripts_dir, name_filter=args.filter, history_idx=history_idx)
    print(f"  found {len(pairs)} pair(s), {len(skipped)} unpaired", file=sys.stderr)
    if args.limit:
        pairs = pairs[:args.limit]
        print(f"  limited to {len(pairs)}", file=sys.stderr)

    sections: list[str] = []
    sections.append(f"# Local Memo Transcriber — v3 vs v5.3 comparison\n")
    sections.append(f"_Generated {datetime.now().isoformat(timespec='seconds')} by `compare_transcripts.py`._\n")
    sections.append(
        "This report pairs every v3 (Whisper fine-tune) transcript with its v5.3 (Conformer / cohere_asr) "
        "counterpart by exact audio duration, and compares them on length, structure, hallucination "
        "patterns, inter-model agreement, top divergence regions, and capitalized-token entity overlap.\n"
    )

    pairs_summary: list[dict[str, Any]] = []
    t0 = time.time()
    per_pair_sections: list[str] = []
    for idx, (v3, v5) in enumerate(pairs, 1):
        section, summary = per_pair_report(v3, v5, idx)
        summary["basename"] = v5.json_path.stem
        pairs_summary.append(summary)
        per_pair_sections.append(section)
        print(f"  [{idx}/{len(pairs)}] {v5.json_path.stem[:50]:50s}  done", file=sys.stderr)

    sections.append(render_aggregate(pairs_summary))
    sections.append("\n## Per-pair reports\n")
    sections.extend(per_pair_sections)

    if skipped:
        sections.append("## Appendix: unpaired transcripts\n")
        sections.append(md_table(
            ["reason", "file"],
            [[r, f] for r, f in sorted(skipped)],
        ))

    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(sections), encoding="utf-8")
    elapsed = time.time() - t0
    print(f"Wrote {out_path}  ({len(pairs)} pairs in {elapsed:.1f}s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
