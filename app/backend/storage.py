import json
import os
import threading
from typing import Any, Dict, List, Optional, Protocol

from .constants import UNSET
from .models import JobRecord, utc_now

try:
    import psycopg
    from psycopg.types.json import Jsonb
except Exception:  # pragma: no cover - only relevant when Postgres is enabled.
    psycopg = None
    Jsonb = None


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgres://"):
        return "postgresql://" + database_url[len("postgres://") :]
    return database_url


def build_deck_summary(
    *,
    filename: str,
    content_type: Optional[str],
    size_bytes: int,
    extracted_text: Optional[str],
    num_pages_or_slides: Optional[int],
) -> dict:
    return {
        "filename": filename,
        "content_type": content_type,
        "size_bytes": int(size_bytes),
        "text_excerpt": (extracted_text or "")[:500],
        "num_pages_or_slides": num_pages_or_slides,
    }


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
        result: object = UNSET,
        llm_test_output: object = UNSET,
        summary_json: object = UNSET,
        summary_error: object = UNSET,
        error: object = UNSET,
    ) -> None:
        pass

    def get_deck_text(self, job_id: str) -> Optional[str]:
        pass

    def save_deck_asset(
        self,
        job_id: str,
        *,
        filename: str,
        content_type: Optional[str],
        size_bytes: int,
        storage_path: str,
        extracted_text: str,
        extracted_json: Optional[List[dict]],
        num_pages_or_slides: Optional[int],
    ) -> None:
        pass

    def delete_job(self, job_id: str) -> None:
        pass


class InMemoryJobStore:
    storage_name = "memory"

    def __init__(self) -> None:
        self._jobs: Dict[str, JobRecord] = {}
        self._deck_text_by_job: Dict[str, str] = {}
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
                deck=None,
                llm_test_output=None,
                summary_json=None,
                summary_error=None,
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
        result: object = UNSET,
        llm_test_output: object = UNSET,
        summary_json: object = UNSET,
        summary_error: object = UNSET,
        error: object = UNSET,
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if status is not None:
                job.status = status
            if progress is not None:
                job.progress = progress
            if result is not UNSET:
                job.result = result
            if llm_test_output is not UNSET:
                job.llm_test_output = llm_test_output
            if summary_json is not UNSET:
                job.summary_json = summary_json
            if summary_error is not UNSET:
                job.summary_error = summary_error
            if error is not UNSET:
                job.error = error
            job.updated_at = utc_now()

    def save_deck_asset(
        self,
        job_id: str,
        *,
        filename: str,
        content_type: Optional[str],
        size_bytes: int,
        storage_path: str,
        extracted_text: str,
        extracted_json: Optional[List[dict]],
        num_pages_or_slides: Optional[int],
    ) -> None:
        del storage_path, extracted_json
        with self._lock:
            job = self._jobs[job_id]
            self._deck_text_by_job[job_id] = extracted_text
            job.deck = build_deck_summary(
                filename=filename,
                content_type=content_type,
                size_bytes=size_bytes,
                extracted_text=extracted_text,
                num_pages_or_slides=num_pages_or_slides,
            )
            job.updated_at = utc_now()

    def get_deck_text(self, job_id: str) -> Optional[str]:
        with self._lock:
            return self._deck_text_by_job.get(job_id)

    def delete_job(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
            self._deck_text_by_job.pop(job_id, None)


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
                        summary_json JSONB NULL,
                        summary_error TEXT NULL,
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
                cur.execute(
                    """
                    ALTER TABLE transcription_jobs
                    ADD COLUMN IF NOT EXISTS llm_test_output TEXT NULL
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE transcription_jobs
                    ADD COLUMN IF NOT EXISTS summary_json JSONB NULL
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE transcription_jobs
                    ADD COLUMN IF NOT EXISTS summary_error TEXT NULL
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS deck_assets (
                        job_id UUID PRIMARY KEY
                            REFERENCES transcription_jobs(job_id)
                            ON DELETE CASCADE,
                        filename TEXT NOT NULL,
                        content_type TEXT NULL,
                        size_bytes BIGINT NOT NULL,
                        storage_path TEXT NOT NULL,
                        extracted_text TEXT NULL,
                        extracted_json JSONB NULL,
                        num_pages_or_slides INTEGER NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )

    def create_job(self, job_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO transcription_jobs (
                        job_id, status, progress, result, llm_test_output, summary_json, summary_error, error
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (job_id, "queued", 0, None, None, None, None, None),
                )

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        tj.created_at,
                        tj.updated_at,
                        tj.status,
                        tj.progress,
                        tj.result,
                        tj.llm_test_output,
                        tj.summary_json,
                        tj.summary_error,
                        tj.error,
                        da.filename,
                        da.content_type,
                        da.size_bytes,
                        da.num_pages_or_slides,
                        LEFT(COALESCE(da.extracted_text, ''), 500) AS text_excerpt
                    FROM transcription_jobs tj
                    LEFT JOIN deck_assets da ON da.job_id = tj.job_id
                    WHERE tj.job_id = %s
                    """,
                    (job_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None

                (
                    created_at,
                    updated_at,
                    status,
                    progress,
                    result,
                    llm_test_output,
                    summary_json,
                    summary_error,
                    error,
                    deck_filename,
                    deck_content_type,
                    deck_size_bytes,
                    deck_num_pages_or_slides,
                    deck_text_excerpt,
                ) = row

                if result is not None and isinstance(result, str):
                    result = json.loads(result)
                if summary_json is not None and isinstance(summary_json, str):
                    summary_json = json.loads(summary_json)

                deck = None
                if deck_filename:
                    deck = {
                        "filename": deck_filename,
                        "content_type": deck_content_type,
                        "size_bytes": int(deck_size_bytes or 0),
                        "text_excerpt": deck_text_excerpt or "",
                        "num_pages_or_slides": deck_num_pages_or_slides,
                    }

                return JobRecord(
                    created_at=created_at,
                    updated_at=updated_at,
                    status=status,
                    progress=progress,
                    result=result,
                    deck=deck,
                    llm_test_output=llm_test_output,
                    summary_json=summary_json,
                    summary_error=summary_error,
                    error=error,
                )

    def update_job(
        self,
        job_id: str,
        *,
        status: Optional[str] = None,
        progress: Optional[int] = None,
        result: object = UNSET,
        llm_test_output: object = UNSET,
        summary_json: object = UNSET,
        summary_error: object = UNSET,
        error: object = UNSET,
    ) -> None:
        assignments: List[str] = []
        values: List[Any] = []

        if status is not None:
            assignments.append("status = %s")
            values.append(status)
        if progress is not None:
            assignments.append("progress = %s")
            values.append(progress)
        if result is not UNSET:
            assignments.append("result = %s")
            values.append(Jsonb(result) if result is not None else None)
        if llm_test_output is not UNSET:
            assignments.append("llm_test_output = %s")
            values.append(llm_test_output)
        if summary_json is not UNSET:
            assignments.append("summary_json = %s")
            values.append(Jsonb(summary_json) if summary_json is not None else None)
        if summary_error is not UNSET:
            assignments.append("summary_error = %s")
            values.append(summary_error)
        if error is not UNSET:
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

    def save_deck_asset(
        self,
        job_id: str,
        *,
        filename: str,
        content_type: Optional[str],
        size_bytes: int,
        storage_path: str,
        extracted_text: str,
        extracted_json: Optional[List[dict]],
        num_pages_or_slides: Optional[int],
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO deck_assets (
                        job_id,
                        filename,
                        content_type,
                        size_bytes,
                        storage_path,
                        extracted_text,
                        extracted_json,
                        num_pages_or_slides
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (job_id) DO UPDATE SET
                        filename = EXCLUDED.filename,
                        content_type = EXCLUDED.content_type,
                        size_bytes = EXCLUDED.size_bytes,
                        storage_path = EXCLUDED.storage_path,
                        extracted_text = EXCLUDED.extracted_text,
                        extracted_json = EXCLUDED.extracted_json,
                        num_pages_or_slides = EXCLUDED.num_pages_or_slides
                    """,
                    (
                        job_id,
                        filename,
                        content_type,
                        size_bytes,
                        storage_path,
                        extracted_text,
                        Jsonb(extracted_json) if extracted_json is not None else None,
                        num_pages_or_slides,
                    ),
                )

    def get_deck_text(self, job_id: str) -> Optional[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT extracted_text FROM deck_assets WHERE job_id = %s", (job_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                return row[0]

    def delete_job(self, job_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM transcription_jobs WHERE job_id = %s", (job_id,))


def build_job_store() -> JobStore:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return PostgresJobStore(database_url=database_url)
    return InMemoryJobStore()
