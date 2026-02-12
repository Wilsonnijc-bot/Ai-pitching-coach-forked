from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def duration_to_seconds(duration) -> float:
    if duration is None:
        return 0.0
    seconds = getattr(duration, "seconds", 0) or 0
    nanos = getattr(duration, "nanos", 0) or 0
    return float(seconds) + (float(nanos) / 1_000_000_000.0)


@dataclass
class JobRecord:
    created_at: datetime
    updated_at: datetime
    status: str
    progress: int
    result: Optional[dict] = None
    deck: Optional[dict] = None
    llm_test_output: Optional[str] = None
    summary_json: Optional[dict] = None
    summary_error: Optional[str] = None
    artifacts_gcs_prefix: Optional[str] = None
    has_diarization: Optional[bool] = None
    artifacts_error: Optional[str] = None
    error: Optional[str] = None


class CreateJobResponse(BaseModel):
    job_id: str
    status: str


class LLMTestResponse(BaseModel):
    job_id: str
    status: str
    llm_test_output: str


class SummarizeResponse(BaseModel):
    job_id: str
    status: str


class Segment(BaseModel):
    start: float
    end: float
    text: str


class Word(BaseModel):
    start: float
    end: float
    word: str


class TranscriptResult(BaseModel):
    full_text: str
    segments: List[Segment]
    words: List[Word]


class DeckInfo(BaseModel):
    filename: str
    content_type: Optional[str]
    size_bytes: int
    text_excerpt: str
    num_pages_or_slides: Optional[int]


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    transcript: Optional[TranscriptResult]
    deck: Optional[DeckInfo]
    llm_test_output: Optional[str]
    summary: Optional[Dict[str, object]]
    summary_error: Optional[str]
    # Backward-compatible alias for older frontend clients.
    result: Optional[TranscriptResult] = None
    error: Optional[str]
