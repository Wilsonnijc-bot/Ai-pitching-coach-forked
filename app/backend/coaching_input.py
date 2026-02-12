from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from .metrics import compute_derived_metrics
from .storage import JobStore


class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float
    speaker: Optional[str] = None


class DerivedMetrics(BaseModel):
    duration_seconds: float
    wpm: float
    pause_count: int
    longest_pause_seconds: float
    filler_count: int
    filler_rate_per_min: float
    top_fillers: list[dict]


class SharedCoachingInput(BaseModel):
    job_id: str
    transcript_full_text: str
    words: list[WordTimestamp]
    derived_metrics: DerivedMetrics
    deck_text: str


def _safe_list_of_dicts(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _extract_transcript_payload(job) -> tuple[str, list[dict], list[dict]]:
    payload = job.result if isinstance(job.result, dict) else {}
    full_text = str(job.transcript_full_text or payload.get("full_text") or "").strip()
    words = _safe_list_of_dicts(job.transcript_words if job.transcript_words is not None else payload.get("words"))
    segments = _safe_list_of_dicts(
        job.transcript_segments if job.transcript_segments is not None else payload.get("segments")
    )
    return full_text, words, segments


def load_shared_input(job_store: JobStore, job_id: str) -> SharedCoachingInput:
    job = job_store.get_job(job_id)
    if not job:
        raise RuntimeError(f"Job not found: {job_id}")

    transcript_full_text, words, segments = _extract_transcript_payload(job)
    if not transcript_full_text:
        raise RuntimeError("Transcript full text is missing for this job.")

    derived_metrics_dict: dict
    if isinstance(job.derived_metrics, dict):
        derived_metrics_dict = job.derived_metrics
    else:
        derived_metrics_dict = compute_derived_metrics(words)

    # Backfill shared input columns for older jobs that only had `result`.
    updates: dict[str, Any] = {}
    if not job.transcript_full_text:
        updates["transcript_full_text"] = transcript_full_text
    if job.transcript_words is None:
        updates["transcript_words"] = words
    if job.transcript_segments is None:
        updates["transcript_segments"] = segments
    if job.derived_metrics is None:
        updates["derived_metrics"] = derived_metrics_dict
    if updates:
        job_store.update_job(job_id, **updates)

    deck_text = (job_store.get_deck_text(job_id) or "").strip()
    shared_input = SharedCoachingInput(
        job_id=job_id,
        transcript_full_text=transcript_full_text,
        words=[
            WordTimestamp(
                word=str(item.get("word") or ""),
                start=float(item.get("start") or 0.0),
                end=float(item.get("end") or 0.0),
                speaker=(str(item.get("speaker")) if item.get("speaker") is not None else None),
            )
            for item in words
        ],
        derived_metrics=DerivedMetrics(**derived_metrics_dict),
        deck_text=deck_text,
    )
    return shared_input
