import base64
import json
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from google.api_core.exceptions import GoogleAPICallError
from google.cloud import speech
from google.oauth2 import service_account
from pydantic import BaseModel

try:
    import psycopg
    from psycopg.types.json import Jsonb
except Exception:  # pragma: no cover - only relevant when Postgres is enabled.
    psycopg = None
    Jsonb = None


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
_UNSET = object()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def duration_to_seconds(duration) -> float:
    if duration is None:
        return 0.0
    seconds = getattr(duration, "seconds", 0) or 0
    nanos = getattr(duration, "nanos", 0) or 0
    return float(seconds) + (float(nanos) / 1_000_000_000.0)


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgres://"):
        return "postgresql://" + database_url[len("postgres://") :]
    return database_url


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


class JobStore(Protocol):
    storage_name: str

    def create_job(self, job_id: str) -> None:
        pass

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        pass

    def update_job(
        self,
        job_id: str,
        *,
        status: Optional[str] = None,
        progress: Optional[int] = None,
        result: object = _UNSET,
        error: object = _UNSET,
    ) -> None:
        pass

    def delete_job(self, job_id: str) -> None:
        pass


class InMemoryJobStore:
    storage_name = "memory"

    def __init__(self) -> None:
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def create_job(self, job_id: str) -> None:
        now = utc_now()
        with self._lock:
            self._jobs[job_id] = JobRecord(
                created_at=now,
                updated_at=now,
                status="queued",
                progress=0,
                result=None,
                error=None,
            )

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            return self._jobs.get(job_id)

    def update_job(
        self,
        job_id: str,
        *,
        status: Optional[str] = None,
        progress: Optional[int] = None,
        result: object = _UNSET,
        error: object = _UNSET,
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if status is not None:
                job.status = status
            if progress is not None:
                job.progress = progress
            if result is not _UNSET:
                job.result = result
            if error is not _UNSET:
                job.error = error
            job.updated_at = utc_now()

    def delete_job(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)


class PostgresJobStore:
    storage_name = "postgres"

    def __init__(self, database_url: str) -> None:
        if psycopg is None or Jsonb is None:
            raise RuntimeError("psycopg is required when DATABASE_URL is set.")
        self._database_url = normalize_database_url(database_url)
        self._ensure_schema()

    def _connect(self):
        return psycopg.connect(self._database_url, autocommit=True)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS transcription_jobs (
                        job_id UUID PRIMARY KEY,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        status TEXT NOT NULL,
                        progress INTEGER NOT NULL CHECK (progress BETWEEN 0 AND 100),
                        result JSONB NULL,
                        error TEXT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_transcription_jobs_updated_at
                    ON transcription_jobs (updated_at DESC)
                    """
                )

    def create_job(self, job_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO transcription_jobs (job_id, status, progress, result, error)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (job_id, "queued", 0, None, None),
                )

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT created_at, updated_at, status, progress, result, error
                    FROM transcription_jobs
                    WHERE job_id = %s
                    """,
                    (job_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                created_at, updated_at, status, progress, result, error = row
                if result is not None and isinstance(result, str):
                    result = json.loads(result)
                return JobRecord(
                    created_at=created_at,
                    updated_at=updated_at,
                    status=status,
                    progress=progress,
                    result=result,
                    error=error,
                )

    def update_job(
        self,
        job_id: str,
        *,
        status: Optional[str] = None,
        progress: Optional[int] = None,
        result: object = _UNSET,
        error: object = _UNSET,
    ) -> None:
        assignments: List[str] = []
        values: List[Any] = []

        if status is not None:
            assignments.append("status = %s")
            values.append(status)
        if progress is not None:
            assignments.append("progress = %s")
            values.append(progress)
        if result is not _UNSET:
            assignments.append("result = %s")
            values.append(Jsonb(result) if result is not None else None)
        if error is not _UNSET:
            assignments.append("error = %s")
            values.append(error)

        assignments.append("updated_at = NOW()")
        values.append(job_id)

        query = f"UPDATE transcription_jobs SET {', '.join(assignments)} WHERE job_id = %s"

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, values)
                if cur.rowcount == 0:
                    raise KeyError(f"Job {job_id} not found.")

    def delete_job(self, job_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM transcription_jobs WHERE job_id = %s", (job_id,))


def build_job_store() -> JobStore:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return PostgresJobStore(database_url=database_url)
    return InMemoryJobStore()


def load_service_account_credentials():
    credentials_b64 = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_B64", "").strip()
    credentials_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

    if credentials_b64:
        try:
            payload = base64.b64decode(credentials_b64).decode("utf-8")
            info = json.loads(payload)
        except Exception as exc:
            raise RuntimeError(f"Invalid GOOGLE_APPLICATION_CREDENTIALS_B64: {exc}") from exc
        return service_account.Credentials.from_service_account_info(info)

    if credentials_json:
        try:
            info = json.loads(credentials_json)
        except Exception as exc:
            raise RuntimeError(f"Invalid GOOGLE_APPLICATION_CREDENTIALS_JSON: {exc}") from exc
        return service_account.Credentials.from_service_account_info(info)

    if credentials_path:
        if not Path(credentials_path).exists():
            raise RuntimeError(
                f"GOOGLE_APPLICATION_CREDENTIALS points to a missing file: {credentials_path}"
            )
        return None

    raise RuntimeError(
        "Google credentials are not configured. Set one of "
        "GOOGLE_APPLICATION_CREDENTIALS, GOOGLE_APPLICATION_CREDENTIALS_JSON, "
        "or GOOGLE_APPLICATION_CREDENTIALS_B64."
    )


def build_speech_client() -> speech.SpeechClient:
    credentials = load_service_account_credentials()
    if credentials is None:
        return speech.SpeechClient()
    return speech.SpeechClient(credentials=credentials)


app = FastAPI(title="AI Pitching Coach Backend")
job_store = build_job_store()

frontend_origins = os.getenv(
    "FRONTEND_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in frontend_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_upload_size(request, call_next):
    if request.method == "POST" and request.url.path == "/api/jobs":
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_UPLOAD_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"Upload too large. Max size is {MAX_UPLOAD_BYTES} bytes."},
                    )
            except ValueError:
                pass
    return await call_next(request)


def update_job(
    job_id: str,
    *,
    status: Optional[str] = None,
    progress: Optional[int] = None,
    result: object = _UNSET,
    error: object = _UNSET,
) -> None:
    job_store.update_job(
        job_id,
        status=status,
        progress=progress,
        result=result,
        error=error,
    )


async def write_upload_to_disk(upload: UploadFile, destination: Path) -> int:
    total_bytes = 0
    with destination.open("wb") as output:
        while True:
            chunk = await upload.read(CHUNK_SIZE)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Upload too large. Max size is {MAX_UPLOAD_BYTES} bytes.",
                )
            output.write(chunk)

    await upload.close()
    if total_bytes == 0:
        raise HTTPException(status_code=400, detail="Audio file is empty.")
    return total_bytes


def parse_speech_response(response) -> dict:
    full_text_parts: List[str] = []
    segments: List[dict] = []
    words: List[dict] = []

    for result in response.results:
        if not result.alternatives:
            continue
        alternative = result.alternatives[0]
        transcript = (alternative.transcript or "").strip()
        if transcript:
            full_text_parts.append(transcript)

        result_words = list(alternative.words or [])
        if result_words:
            segment_start = duration_to_seconds(result_words[0].start_time)
            segment_end = duration_to_seconds(result_words[-1].end_time)
        else:
            segment_start = 0.0
            segment_end = 0.0

        segments.append(
            {
                "start": segment_start,
                "end": segment_end,
                "text": transcript,
            }
        )

        for word_info in result_words:
            words.append(
                {
                    "start": duration_to_seconds(word_info.start_time),
                    "end": duration_to_seconds(word_info.end_time),
                    "word": word_info.word,
                }
            )

    return {
        "full_text": " ".join(full_text_parts).strip(),
        "segments": segments,
        "words": words,
    }


def process_transcription_job(job_id: str, input_path: Path, temp_dir: Path) -> None:
    try:
        update_job(job_id, status="transcribing", progress=20, error=None)

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            raise RuntimeError(
                "ffmpeg is not installed or not on PATH. Install ffmpeg (macOS: brew install ffmpeg)."
            )

        wav_path = temp_dir / "converted.wav"
        command = [
            ffmpeg_path,
            "-y",
            "-i",
            str(input_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            str(wav_path),
        ]
        ffmpeg_result = subprocess.run(command, capture_output=True, text=True)
        if ffmpeg_result.returncode != 0:
            stderr_tail = (ffmpeg_result.stderr or "").strip().splitlines()
            message = stderr_tail[-1] if stderr_tail else "Unknown ffmpeg error"
            raise RuntimeError(f"Audio conversion failed: {message}")

        update_job(job_id, progress=60)

        with wav_path.open("rb") as wav_file:
            wav_content = wav_file.read()
        if not wav_content:
            raise RuntimeError("Converted WAV audio is empty.")

        client = build_speech_client()
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code="en-US",
            enable_automatic_punctuation=True,
            enable_word_time_offsets=True,
        )
        audio = speech.RecognitionAudio(content=wav_content)
        response = client.recognize(config=config, audio=audio)

        update_job(job_id, progress=90)
        result = parse_speech_response(response)
        update_job(job_id, status="done", progress=100, result=result, error=None)
    except GoogleAPICallError as exc:
        update_job(job_id, status="failed", progress=100, error=f"Google Speech-to-Text error: {exc}")
    except Exception as exc:
        update_job(job_id, status="failed", progress=100, error=str(exc))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "storage": job_store.storage_name}


@app.post("/api/jobs", response_model=CreateJobResponse)
async def create_transcription_job(
    background_tasks: BackgroundTasks,
    audio: Optional[UploadFile] = File(None),
) -> CreateJobResponse:
    if audio is None:
        raise HTTPException(status_code=400, detail="Missing audio file.")

    job_id = str(uuid.uuid4())
    job_store.create_job(job_id)

    temp_dir = Path(tempfile.mkdtemp(prefix=f"job_{job_id}_"))
    suffix = Path(audio.filename or "").suffix or ".webm"
    input_path = temp_dir / f"input{suffix}"

    try:
        await write_upload_to_disk(audio, input_path)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        job_store.delete_job(job_id)
        raise

    background_tasks.add_task(process_transcription_job, job_id, input_path, temp_dir)
    return CreateJobResponse(job_id=job_id, status="queued")


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str) -> JobStatusResponse:
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobStatusResponse(
        job_id=job_id,
        status=job.status,
        progress=job.progress,
        result=job.result,
        error=job.error,
    )


frontend_dir = Path(__file__).resolve().parent / "app" / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
