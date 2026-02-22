from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .coaching_round1 import run_round1
from .coaching_round2 import run_round2
from .coaching_round3 import run_round3
from .coaching_round4 import run_round4
from .coaching_round5 import run_round5
from .storage import JobStore


logger = logging.getLogger("uvicorn.error")

ROUND_RUNNERS = {
    1: run_round1,
    2: run_round2,
    3: run_round3,
    4: run_round4,
}

_active_jobs_lock = threading.Lock()
_active_jobs: set[str] = set()


def _has_transcript(job) -> bool:
    transcript_payload = job.result if isinstance(job.result, dict) else {}
    transcript_text = str(job.transcript_full_text or transcript_payload.get("full_text") or "").strip()
    return bool(transcript_text)


def _round_done(job, round_number: int) -> bool:
    status = getattr(job, f"feedback_round_{round_number}_status", None)
    payload = getattr(job, f"feedback_round_{round_number}", None)
    return status == "done" and isinstance(payload, dict)


def _missing_prerequisites(job) -> list[str]:
    missing: list[str] = []
    for round_number in (1, 2, 3, 4):
        if not _round_done(job, round_number):
            status = getattr(job, f"feedback_round_{round_number}_status", "pending")
            missing.append(f"round{round_number} ({status})")
    return missing


def _mark_round5_skipped(job_store: JobStore, job_id: str, missing: list[str]) -> None:
    detail = ", ".join(missing) if missing else "unknown prerequisites"
    message = f"Round 5 skipped because prerequisite rounds are incomplete or failed: {detail}"
    job_store.update_job(
        job_id,
        feedback_round_5_status="failed",
        feedback_round_5_error=message,
        feedback_round_5_version="r5_v2",
    )


def _run_feedback_orchestration(job_store: JobStore, job_id: str, source: str) -> None:
    start_ts = time.monotonic()
    first_failed_round: int | None = None

    try:
        job = job_store.get_job(job_id)
        if not job:
            logger.warning("job_id=%s feedback_orchestration_abort reason=job_not_found source=%s", job_id, source)
            return

        if not _has_transcript(job):
            logger.warning(
                "job_id=%s feedback_orchestration_abort reason=transcript_missing source=%s",
                job_id,
                source,
            )
            return

        rounds_to_run = [round_number for round_number in (1, 2, 3, 4) if not _round_done(job, round_number)]
        logger.info(
            "job_id=%s feedback_orchestration_started source=%s rounds_to_run=%s",
            job_id,
            source,
            rounds_to_run,
        )

        if rounds_to_run:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {
                    pool.submit(ROUND_RUNNERS[round_number], job_store, job_id): round_number
                    for round_number in rounds_to_run
                }

                for future in as_completed(futures):
                    round_number = futures[future]
                    try:
                        future.result()
                        logger.info(
                            "job_id=%s feedback_round_completed round=%s source=%s",
                            job_id,
                            round_number,
                            source,
                        )
                    except Exception as exc:
                        if first_failed_round is None:
                            first_failed_round = round_number
                            logger.warning(
                                "job_id=%s feedback_orchestration_first_failure round=%s source=%s error=%s",
                                job_id,
                                round_number,
                                source,
                                exc,
                            )
                            for other_future, other_round in futures.items():
                                if other_future is future:
                                    continue
                                if other_future.done():
                                    continue
                                cancelled = other_future.cancel()
                                logger.info(
                                    "job_id=%s feedback_orchestration_cancel_attempt target_round=%s cancelled=%s source=%s",
                                    job_id,
                                    other_round,
                                    cancelled,
                                    source,
                                )
                        else:
                            logger.warning(
                                "job_id=%s feedback_orchestration_additional_failure round=%s source=%s error=%s",
                                job_id,
                                round_number,
                                source,
                                exc,
                            )

        latest = job_store.get_job(job_id)
        if not latest:
            logger.warning(
                "job_id=%s feedback_orchestration_abort reason=job_missing_after_rounds source=%s",
                job_id,
                source,
            )
            return

        missing = _missing_prerequisites(latest)
        if first_failed_round is not None or missing:
            if _round_done(latest, 5):
                logger.info(
                    "job_id=%s feedback_orchestration_round5_preserved_done source=%s",
                    job_id,
                    source,
                )
                return
            _mark_round5_skipped(job_store, job_id, missing)
            logger.warning(
                "job_id=%s feedback_orchestration_round5_skipped source=%s first_failed_round=%s missing=%s",
                job_id,
                source,
                first_failed_round,
                missing,
            )
            return

        if _round_done(latest, 5):
            logger.info(
                "job_id=%s feedback_orchestration_round5_already_done source=%s",
                job_id,
                source,
            )
            return

        logger.info("job_id=%s feedback_orchestration_round5_start source=%s", job_id, source)
        run_round5(job_store, job_id)
        logger.info("job_id=%s feedback_orchestration_round5_done source=%s", job_id, source)
    except Exception:
        logger.error(
            "job_id=%s feedback_orchestration_unhandled_error source=%s",
            job_id,
            source,
            exc_info=True,
        )
    finally:
        elapsed_ms = int((time.monotonic() - start_ts) * 1000)
        with _active_jobs_lock:
            _active_jobs.discard(job_id)
        logger.info(
            "job_id=%s feedback_orchestration_finished source=%s elapsed_ms=%s",
            job_id,
            source,
            elapsed_ms,
        )


def ensure_feedback_orchestration_started(job_store: JobStore, job_id: str, source: str) -> bool:
    with _active_jobs_lock:
        if job_id in _active_jobs:
            logger.info(
                "job_id=%s feedback_orchestration_already_running source=%s",
                job_id,
                source,
            )
            return False
        _active_jobs.add(job_id)

    thread = threading.Thread(
        target=_run_feedback_orchestration,
        args=(job_store, job_id, source),
        daemon=True,
    )
    thread.start()
    return True
