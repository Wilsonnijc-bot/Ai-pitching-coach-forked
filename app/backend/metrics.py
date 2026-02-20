from __future__ import annotations

import logging
import math
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional


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


logger = logging.getLogger("uvicorn.error")


# ---------------------------------------------------------------------------
# Tone / audio-signal metrics (requires librosa + numpy)
# ---------------------------------------------------------------------------

def compute_energy_timeline(
    wav_path: Path,
    words: list[dict],
    sr: int = 16000,
) -> Optional[list[dict]]:
    """Per-second RMS (dB) + F0 (Hz) mapped to transcript text.

    Returns a list of dicts: [{sec, text, rms_db, f0_hz}, ...] or None on error.
    """
    try:
        import librosa
        import numpy as np
    except ImportError:
        logger.warning("librosa/numpy not installed — skipping energy timeline")
        return None

    try:
        y, _ = librosa.load(str(wav_path), sr=sr, mono=True)
        duration_sec = len(y) / sr
        total_seconds = int(math.ceil(duration_sec))
        if total_seconds == 0:
            return None

        # --- Per-second RMS ---
        hop_length = sr  # 1 second per frame
        rms = librosa.feature.rms(y=y, frame_length=sr, hop_length=hop_length, center=True)[0]

        # --- Per-second F0 ---
        #
        # NOTE:
        # Using pyin with 1-second hop on some recordings triggers:
        # "ParameterError: Target size (...) must be at least input size (...)"
        # in librosa's sequence.transition_local path. To keep this robust in
        # production, we estimate pitch at a smaller hop and aggregate to
        # per-second median F0. If pyin fails, we fall back to yin.
        pitch_hop = max(256, sr // 50)  # ~20 ms at 16kHz
        f0_hz_by_sec: list[Optional[float]] = [None] * total_seconds

        def _aggregate_pitch_to_seconds(f0_series, frame_times) -> list[Optional[float]]:
            buckets: list[list[float]] = [[] for _ in range(total_seconds)]
            for hz, t in zip(f0_series, frame_times):
                if hz is None:
                    continue
                hz_val = float(hz)
                if not np.isfinite(hz_val) or hz_val <= 0:
                    continue
                sec_idx = int(float(t))
                if 0 <= sec_idx < total_seconds:
                    buckets[sec_idx].append(hz_val)
            return [
                round(float(np.median(bucket)), 1) if bucket else None
                for bucket in buckets
            ]

        try:
            f0_pyin, _, _ = librosa.pyin(
                y,
                fmin=60,
                fmax=500,
                sr=sr,
                frame_length=2048,
                hop_length=pitch_hop,
            )
            frame_times = librosa.frames_to_time(
                np.arange(len(f0_pyin)),
                sr=sr,
                hop_length=pitch_hop,
            )
            f0_hz_by_sec = _aggregate_pitch_to_seconds(f0_pyin, frame_times)
        except Exception:
            logger.warning("pyin pitch extraction failed; falling back to yin", exc_info=True)
            try:
                f0_yin = librosa.yin(
                    y,
                    fmin=60,
                    fmax=500,
                    sr=sr,
                    frame_length=2048,
                    hop_length=pitch_hop,
                )
                frame_times = librosa.frames_to_time(
                    np.arange(len(f0_yin)),
                    sr=sr,
                    hop_length=pitch_hop,
                )
                f0_hz_by_sec = _aggregate_pitch_to_seconds(f0_yin, frame_times)
            except Exception:
                logger.warning("yin pitch extraction failed", exc_info=True)

        # --- Map words into 1-second bins ---
        sorted_words = sorted(words, key=lambda w: _to_float(w.get("start"), 0.0))
        bins: dict[int, list[str]] = {}
        for w in sorted_words:
            token = str(w.get("word") or "").strip()
            if not token:
                continue
            start = _to_float(w.get("start"), 0.0)
            end = _to_float(w.get("end"), 0.0)
            mid = (start + end) / 2.0
            sec_idx = int(mid)
            bins.setdefault(sec_idx, []).append(token)

        timeline: list[dict] = []
        for sec_idx in range(total_seconds):
            text = " ".join(bins.get(sec_idx, [])) or "(pause)"
            rms_val = float(rms[sec_idx]) if sec_idx < len(rms) else 0.0
            rms_db = round(20 * math.log10(max(rms_val, 1e-10)), 1)
            f0_hz = f0_hz_by_sec[sec_idx] if sec_idx < len(f0_hz_by_sec) else None
            timeline.append({
                "sec": sec_idx,
                "text": text,
                "rms_db": rms_db,
                "f0_hz": f0_hz,
            })

        return timeline
    except Exception:
        logger.warning("energy_timeline computation failed", exc_info=True)
        return None


def compute_sentence_pacing(words: list[dict]) -> Optional[list[dict]]:
    """Per-sentence WPM computed from word timestamps.

    Splits transcript into sentences using punctuation-based heuristics,
    then computes speaking speed for each sentence.

    Returns: [{sentence, wpm, duration_sec, start, end}, ...] or None.
    """
    if not words:
        return None

    try:
        sorted_words = sorted(words, key=lambda w: _to_float(w.get("start"), 0.0))

        # Build sentences by splitting on sentence-ending punctuation
        sentences: list[dict] = []
        current_tokens: list[str] = []
        current_start: Optional[float] = None
        current_end: float = 0.0

        for w in sorted_words:
            token = str(w.get("word") or "").strip()
            if not token:
                continue
            start = _to_float(w.get("start"), 0.0)
            end = _to_float(w.get("end"), 0.0)

            if current_start is None:
                current_start = start

            current_tokens.append(token)
            current_end = end

            # Sentence boundary: ends with . ! ? or has been long enough
            stripped = token.rstrip(",;:")
            if stripped.endswith((".", "!", "?")) or len(current_tokens) >= 30:
                sentence_text = " ".join(current_tokens)
                duration = max(current_end - (current_start or 0.0), 0.01)
                word_count = _count_alpha_like_words(
                    [{"word": t} for t in current_tokens]
                )
                wpm = (word_count / duration) * 60.0

                sentences.append({
                    "sentence": sentence_text,
                    "wpm": round(wpm, 0),
                    "duration_sec": round(duration, 2),
                    "start": round(current_start or 0.0, 2),
                    "end": round(current_end, 2),
                })
                current_tokens = []
                current_start = None

        # Remaining tokens (no trailing punctuation)
        if current_tokens:
            sentence_text = " ".join(current_tokens)
            duration = max(current_end - (current_start or 0.0), 0.01)
            word_count = _count_alpha_like_words(
                [{"word": t} for t in current_tokens]
            )
            wpm = (word_count / duration) * 60.0
            sentences.append({
                "sentence": sentence_text,
                "wpm": round(wpm, 0),
                "duration_sec": round(duration, 2),
                "start": round(current_start or 0.0, 2),
                "end": round(current_end, 2),
            })

        return sentences if sentences else None
    except Exception:
        logger.warning("sentence_pacing computation failed", exc_info=True)
        return None
