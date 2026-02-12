from __future__ import annotations

import re
from collections import Counter
from typing import Iterable


PAUSE_THRESHOLD_SECONDS = 0.60
SINGLE_WORD_FILLERS = {
    "um",
    "uh",
    "like",
    "actually",
    "basically",
    "literally",
}
MULTI_WORD_FILLERS = [("you", "know"), ("kind", "of"), ("sort", "of")]


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_token(raw_word: str) -> str:
    lowered = str(raw_word or "").lower().strip()
    return lowered.strip(",.!?;:")


def _count_alpha_like_words(words: Iterable[dict]) -> int:
    count = 0
    for word in words:
        token = str(word.get("word") or "").strip()
        if not token:
            continue
        if re.search(r"[A-Za-z]", token):
            count += 1
    return count


def compute_derived_metrics(words: list[dict]) -> dict:
    if not words:
        return {
            "duration_seconds": 0.0,
            "wpm": 0.0,
            "pause_count": 0,
            "longest_pause_seconds": 0.0,
            "filler_count": 0,
            "filler_rate_per_min": 0.0,
            "top_fillers": [],
        }

    normalized_words = [
        {
            "word": str(item.get("word") or ""),
            "start": _to_float(item.get("start"), 0.0),
            "end": _to_float(item.get("end"), 0.0),
        }
        for item in words
    ]

    start = min(item["start"] for item in normalized_words)
    end = max(item["end"] for item in normalized_words)
    duration_seconds = max(0.0, end - start)

    word_count = _count_alpha_like_words(normalized_words)
    duration_minutes = max(duration_seconds / 60.0, 1e-6)
    wpm = float(word_count) / duration_minutes

    sorted_words = sorted(normalized_words, key=lambda item: item["start"])
    pause_count = 0
    longest_pause_seconds = 0.0

    for current, nxt in zip(sorted_words, sorted_words[1:]):
        gap = _to_float(nxt.get("start"), 0.0) - _to_float(current.get("end"), 0.0)
        if gap >= PAUSE_THRESHOLD_SECONDS:
            pause_count += 1
        if gap > longest_pause_seconds:
            longest_pause_seconds = gap

    tokens = [_normalize_token(item["word"]) for item in sorted_words]
    filler_counter: Counter[str] = Counter()

    for token in tokens:
        if token in SINGLE_WORD_FILLERS:
            filler_counter[token] += 1

    for left, right in zip(tokens, tokens[1:]):
        for first, second in MULTI_WORD_FILLERS:
            if left == first and right == second:
                filler_counter[f"{first} {second}"] += 1

    filler_count = int(sum(filler_counter.values()))
    filler_rate_per_min = float(filler_count) / duration_minutes
    top_fillers = [
        {"token": token, "count": count}
        for token, count in filler_counter.most_common(5)
    ]

    return {
        "duration_seconds": duration_seconds,
        "wpm": wpm,
        "pause_count": pause_count,
        "longest_pause_seconds": max(0.0, longest_pause_seconds),
        "filler_count": filler_count,
        "filler_rate_per_min": filler_rate_per_min,
        "top_fillers": top_fillers,
    }
