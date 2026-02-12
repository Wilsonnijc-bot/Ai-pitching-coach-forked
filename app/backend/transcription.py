import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile
from google.api_core.exceptions import GoogleAPICallError
from google.cloud import speech

from .constants import CHUNK_SIZE, MAX_UPLOAD_BYTES
from .deck_extractor import extract_deck_text
from .google_stt import build_speech_client, parse_speech_response
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


def process_transcription_job(
    job_store: JobStore,
    job_id: str,
    input_path: Path,
    temp_dir: Path,
    deck_upload: Optional[dict] = None,
) -> None:
    try:
        if deck_upload is not None:
            process_deck_asset(job_store, job_id, deck_upload, progress_done=20)

        job_store.update_job(job_id, status="transcribing", progress=30, error=None)

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

        job_store.update_job(job_id, progress=60)

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

        result = parse_speech_response(response)
        job_store.update_job(job_id, progress=90)
        job_store.update_job(job_id, status="done", progress=100, result=result, error=None)

        logger.info(
            "job_id=%s transcript_done full_text_len=%s words=%s",
            job_id,
            len(result.get("full_text", "")),
            len(result.get("words", [])),
        )
    except GoogleAPICallError as exc:
        job_store.update_job(
            job_id, status="failed", progress=100, error=f"Google Speech-to-Text error: {exc}"
        )
    except Exception as exc:
        job_store.update_job(job_id, status="failed", progress=100, error=str(exc))
    finally:
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
