from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

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
    error: Optional[str] = None


class CreateJobResponse(BaseModel):
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


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    result: Optional[TranscriptResult]
    error: Optional[str]
