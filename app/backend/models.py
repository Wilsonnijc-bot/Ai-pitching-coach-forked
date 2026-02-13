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
    transcript_full_text: Optional[str] = None
    transcript_words: Optional[List[dict]] = None
    transcript_segments: Optional[List[dict]] = None
    derived_metrics: Optional[dict] = None
    deck: Optional[dict] = None
    llm_test_output: Optional[str] = None
    summary_json: Optional[dict] = None
    summary_error: Optional[str] = None
    feedback_round_1: Optional[dict] = None
    feedback_round_1_version: Optional[str] = None
    feedback_round_1_status: Optional[str] = None
    feedback_round_1_error: Optional[str] = None
    feedback_round_2: Optional[dict] = None
    feedback_round_2_version: Optional[str] = None
    feedback_round_2_status: Optional[str] = None
    feedback_round_2_error: Optional[str] = None
    feedback_round_3: Optional[dict] = None
    feedback_round_3_version: Optional[str] = None
    feedback_round_3_status: Optional[str] = None
    feedback_round_3_error: Optional[str] = None
    feedback_round_4: Optional[dict] = None
    feedback_round_4_version: Optional[str] = None
    feedback_round_4_status: Optional[str] = None
    feedback_round_4_error: Optional[str] = None
    artifacts_gcs_prefix: Optional[str] = None
    has_diarization: Optional[bool] = None
    artifacts_error: Optional[str] = None
    video_gcs_uri: Optional[str] = None
    error: Optional[str] = None


class CreateJobResponse(BaseModel):
    job_id: str
    status: str


class PrepareJobResponse(BaseModel):
    job_id: str
    upload_url: str
    video_blob_path: str


class LLMTestResponse(BaseModel):
    job_id: str
    status: str
    llm_test_output: str


class SummarizeResponse(BaseModel):
    job_id: str
    status: str


class Round1FeedbackResponse(BaseModel):
    job_id: str
    status: str


class Round2FeedbackResponse(BaseModel):
    job_id: str
    status: str


class Round3FeedbackResponse(BaseModel):
    job_id: str
    status: str


class Round4FeedbackResponse(BaseModel):
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
    speaker: Optional[str] = None


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
    derived_metrics: Optional[Dict[str, object]] = None
    feedback_round_1_status: Optional[str] = None
    feedback_round_1: Optional[Dict[str, object]] = None
    feedback_round_1_version: Optional[str] = None
    feedback_round_1_error: Optional[str] = None
    feedback_round_2_status: Optional[str] = None
    feedback_round_2: Optional[Dict[str, object]] = None
    feedback_round_2_version: Optional[str] = None
    feedback_round_2_error: Optional[str] = None
    feedback_round_3_status: Optional[str] = None
    feedback_round_3: Optional[Dict[str, object]] = None
    feedback_round_3_version: Optional[str] = None
    feedback_round_3_error: Optional[str] = None
    feedback_round_4_status: Optional[str] = None
    feedback_round_4: Optional[Dict[str, object]] = None
    feedback_round_4_version: Optional[str] = None
    feedback_round_4_error: Optional[str] = None
    # Backward-compatible alias for older frontend clients.
    result: Optional[TranscriptResult] = None
    video_gcs_uri: Optional[str] = None
    error: Optional[str]
