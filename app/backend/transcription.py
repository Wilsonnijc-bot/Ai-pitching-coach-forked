import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile

from .constants import CHUNK_SIZE, MAX_UPLOAD_BYTES
from .deck_extractor import extract_deck_text
from .gcs_utils import delete_blob, delete_prefix, get_default_bucket, upload_file
from .stt_v2 import build_audio_blob_path, build_output_prefix, transcribe_v2_chirp2_from_gcs
from .storage import JobStore


logger = logging.getLogger("uvicorn.error")


async def write_upload_to_disk(
    upload: UploadFile,
    destination: Path,
    *,
    field_name: str,
    max_size_bytes: int = MAX_UPLOAD_BYTES,
) -> int:
    total_bytes = 0
    destination.parent.mkdir(parents=True, exist_ok=True)

    with destination.open("wb") as output:
        while True:
            chunk = await upload.read(CHUNK_SIZE)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > max_size_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"{field_name} is too large. Max size is {max_size_bytes} bytes.",
                )
            output.write(chunk)

    await upload.close()
    if total_bytes == 0:
        raise HTTPException(status_code=400, detail=f"{field_name} file is empty.")
    return total_bytes


def process_deck_asset(
    job_store: JobStore,
    job_id: str,
    deck_upload: dict,
    *,
    progress_done: Optional[int] = None,
) -> None:
    job_store.update_job(job_id, status="deck_processing", progress=10, error=None)

    deck_path = Path(deck_upload["storage_path"])
    extraction = extract_deck_text(deck_path)
    job_store.save_deck_asset(
        job_id,
        filename=deck_upload["filename"],
        content_type=deck_upload.get("content_type"),
        size_bytes=deck_upload["size_bytes"],
        storage_path=str(deck_path),
        extracted_text=extraction.extracted_text,
        extracted_json=extraction.extracted_json,
        num_pages_or_slides=extraction.num_pages_or_slides,
    )

    logger.info(
        "job_id=%s deck_processed pages_or_slides=%s text_len=%s",
        job_id,
        extraction.num_pages_or_slides,
        len(extraction.extracted_text),
    )

    if progress_done is not None:
        job_store.update_job(job_id, progress=progress_done)


def parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def convert_audio_to_wav_16khz_mono(input_path: Path, wav_path: Path) -> None:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise RuntimeError(
            "ffmpeg is not installed or not on PATH. Install ffmpeg (macOS: brew install ffmpeg)."
        )

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

    if not wav_path.exists() or wav_path.stat().st_size == 0:
        raise RuntimeError("Converted WAV audio is empty.")


def process_transcription_job(
    job_store: JobStore,
    job_id: str,
    input_path: Path,
    temp_dir: Path,
    deck_upload: Optional[dict] = None,
) -> None:
    bucket_name: Optional[str] = None
    audio_blob_path = build_audio_blob_path(job_id)
    output_prefix = build_output_prefix(job_id)
    uploaded_audio = False

    try:
        if deck_upload is not None:
            process_deck_asset(job_store, job_id, deck_upload, progress_done=10)
        else:
            job_store.update_job(job_id, status="transcribing", progress=10, error=None)

        wav_path = temp_dir / "audio.wav"
        convert_audio_to_wav_16khz_mono(input_path, wav_path)

        bucket_name = get_default_bucket()
        job_store.update_job(job_id, status="uploading_audio_to_gcs", progress=20, error=None)
        gcs_audio_uri = upload_file(
            bucket_name,
            audio_blob_path,
            wav_path,
            content_type="audio/wav",
        )
        uploaded_audio = True

        logger.info(
            "job_id=%s uploaded_audio_to_gcs uri=%s",
            job_id,
            gcs_audio_uri,
        )

        def on_stage(status: str, progress: int) -> None:
            job_store.update_job(job_id, status=status, progress=progress, error=None)

        result = transcribe_v2_chirp2_from_gcs(job_id, gcs_audio_uri, on_stage=on_stage)
        job_store.update_job(job_id, status="done", progress=100, result=result, error=None)

        logger.info(
            "job_id=%s transcript_done full_text_len=%s words=%s",
            job_id,
            len(result.get("full_text", "")),
            len(result.get("words", [])),
        )
    except Exception as exc:
        job_store.update_job(job_id, status="failed", progress=100, error=str(exc))
    finally:
        cleanup_audio = parse_bool_env("GCS_CLEANUP_AUDIO", True)
        cleanup_output = parse_bool_env("GCS_CLEANUP_OUTPUT", True)

        if bucket_name:
            if cleanup_output:
                delete_prefix(output_prefix, bucket=bucket_name)
            if cleanup_audio and uploaded_audio:
                delete_blob(bucket_name, audio_blob_path)

        shutil.rmtree(temp_dir, ignore_errors=True)


def process_deck_only_job(job_store: JobStore, job_id: str, deck_upload: dict) -> None:
    try:
        process_deck_asset(job_store, job_id, deck_upload)

        job = job_store.get_job(job_id)
        if not job:
            return

        if job.status == "deck_processing":
            if job.result:
                job_store.update_job(job_id, status="done", progress=100, error=None)
            else:
                job_store.update_job(job_id, status="queued", progress=20, error=None)
    except Exception as exc:
        job_store.update_job(job_id, status="failed", progress=100, error=str(exc))

