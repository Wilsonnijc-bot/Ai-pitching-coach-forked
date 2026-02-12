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
        error: object = UNSET,
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
        result: object = UNSET,
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
            if error is not UNSET:
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
        result: object = UNSET,
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

    def delete_job(self, job_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM transcription_jobs WHERE job_id = %s", (job_id,))


def build_job_store() -> JobStore:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return PostgresJobStore(database_url=database_url)
    return InMemoryJobStore()
