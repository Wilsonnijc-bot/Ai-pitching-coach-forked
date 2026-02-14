import logging
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile

from .constants import CHUNK_SIZE, MAX_UPLOAD_BYTES
from .deck_extractor import extract_deck_text
from .gcs_utils import (
    build_gs_uri,
    delete_blob,
    delete_prefix,
    get_default_bucket,
    upload_file,
    upload_file_resumable,
    upload_json,
    upload_text,
)
from .metrics import compute_derived_metrics
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


def build_artifacts_prefix(job_id: str) -> str:
    return f"jobs/{job_id}/artifacts/"


def _build_diarization_payload(words: list[dict], gap_seconds: float = 1.0) -> dict:
    has_speaker_tags = any(bool(word.get("speaker")) for word in words)
    if not has_speaker_tags:
        return {
            "speakers": [],
            "word_speaker_tags_present": False,
            "note": "diarization not returned by API/model",
        }

    sorted_words = sorted(words, key=lambda item: (float(item.get("start", 0.0)), float(item.get("end", 0.0))))

    speaker_turns: dict[str, list[dict]] = {}
    speaker_order: list[str] = []
    current_speaker: Optional[str] = None
    current_turn: Optional[dict] = None

    for word in sorted_words:
        speaker = word.get("speaker") or "unknown"
        token = str(word.get("word") or "").strip()
        if not token:
            continue
        start = float(word.get("start", 0.0) or 0.0)
        end = float(word.get("end", 0.0) or 0.0)

        if speaker not in speaker_turns:
            speaker_turns[speaker] = []
            speaker_order.append(speaker)

        needs_new_turn = (
            current_turn is None
            or current_speaker != speaker
            or (start - float(current_turn.get("end", start))) > gap_seconds
        )
        if needs_new_turn:
            current_turn = {"start": start, "end": end, "text": token}
            speaker_turns[speaker].append(current_turn)
        else:
            current_turn["end"] = max(float(current_turn["end"]), end)
            current_turn["text"] = f"{current_turn['text']} {token}".strip()

        current_speaker = speaker

    speakers = [{"speaker": speaker, "turns": speaker_turns.get(speaker, [])} for speaker in speaker_order]
    return {"speakers": speakers, "word_speaker_tags_present": True}


def write_transcript_artifacts(
    *,
    job_id: str,
    bucket_name: str,
    transcript_result: dict,
    model: str,
    location: str,
    diarization_requested: bool,
) -> tuple[str, bool]:
    artifacts_prefix = build_artifacts_prefix(job_id)
    artifacts_prefix_uri = build_gs_uri(bucket_name, artifacts_prefix)

    full_text = str(transcript_result.get("full_text") or "")
    words = list(transcript_result.get("words") or [])
    diarization_payload = _build_diarization_payload(words)
    has_diarization = bool(diarization_payload.get("word_speaker_tags_present"))

    transcript_uri = upload_text(
        bucket_name,
        f"{artifacts_prefix}transcript.txt",
        full_text + "\n",
        content_type="text/plain; charset=utf-8",
    )
    words_uri = upload_json(bucket_name, f"{artifacts_prefix}words.json", words)
    diarization_uri = upload_json(bucket_name, f"{artifacts_prefix}diarization.json", diarization_payload)

    meta_payload = {
        "job_id": job_id,
        "engine": "google-stt-v2",
        "model": model,
        "location": location,
        "bucket": bucket_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "has_words": len(words) > 0,
        "has_diarization": has_diarization,
        "diarization_requested": diarization_requested,
        "artifacts": {
            "transcript_txt": transcript_uri,
            "words_json": words_uri,
            "diarization_json": diarization_uri,
        },
    }
    meta_uri = upload_json(bucket_name, f"{artifacts_prefix}meta.json", meta_payload)

    logger.info(
        "job_id=%s artifacts_written transcript=%s words=%s diarization=%s meta=%s",
        job_id,
        transcript_uri,
        words_uri,
        diarization_uri,
        meta_uri,
    )
    return artifacts_prefix_uri, has_diarization


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
    artifacts_error: Optional[str] = None
    artifacts_gcs_prefix: Optional[str] = None
    has_diarization: Optional[bool] = None

    try:
        if deck_upload is not None:
            process_deck_asset(job_store, job_id, deck_upload, progress_done=10)
        else:
            job_store.update_job(job_id, status="transcribing", progress=10, error=None)

        # --- Upload video to GCS and convert audio in parallel ---
        bucket_name = get_default_bucket()
        video_blob_path = f"jobs/{job_id}/video{input_path.suffix}"
        wav_path = temp_dir / "audio.wav"

        def _upload_video() -> Optional[str]:
            try:
                uri = upload_file_resumable(
                    bucket_name,
                    video_blob_path,
                    input_path,
                    content_type="video/webm",
                )
                job_store.update_job(job_id, video_gcs_uri=uri)
                logger.info("job_id=%s uploaded_video_to_gcs uri=%s", job_id, uri)
                return uri
            except Exception:
                logger.warning("job_id=%s video_upload_failed", job_id, exc_info=True)
                return None

        def _convert_audio() -> None:
            convert_audio_to_wav_16khz_mono(input_path, wav_path)

        with ThreadPoolExecutor(max_workers=2) as pool:
            video_future: Future = pool.submit(_upload_video)
            audio_future: Future = pool.submit(_convert_audio)
            # Wait for audio conversion (critical path); video upload can finish later
            audio_future.result()

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

        stt_payload = transcribe_v2_chirp2_from_gcs(job_id, gcs_audio_uri, on_stage=on_stage)
        transcript_result = stt_payload.get("transcript", {})
        transcript_full_text = str(transcript_result.get("full_text") or "")
        transcript_words = list(transcript_result.get("words") or [])
        transcript_segments = list(transcript_result.get("segments") or [])
        derived_metrics = compute_derived_metrics(transcript_words)
        has_diarization = bool(stt_payload.get("has_diarization", False))

        # --- Compute tone + body-language metrics in parallel ---
        def _compute_tone_metrics() -> dict:
            result = {}
            try:
                from .metrics import compute_energy_timeline, compute_sentence_pacing
                energy_timeline = compute_energy_timeline(wav_path, transcript_words)
                sentence_pacing = compute_sentence_pacing(transcript_words)
                if energy_timeline is not None:
                    result["energy_timeline"] = energy_timeline
                if sentence_pacing is not None:
                    result["sentence_pacing"] = sentence_pacing
            except Exception:
                logger.warning("job_id=%s tone_metrics_failed", job_id, exc_info=True)
            return result

        def _compute_body_language() -> Optional[dict]:
            try:
                from .video_metrics import compute_body_language_metrics
                cal_job = job_store.get_job(job_id)
                cal_data = getattr(cal_job, "calibration_data", None) if cal_job else None
                bl = compute_body_language_metrics(input_path, calibration=cal_data)
                if bl is not None:
                    logger.info(
                        "job_id=%s body_language_metrics_done frames=%s calibrated=%s",
                        job_id,
                        bl.get("summary", {}).get("total_frames_analyzed", 0),
                        bl.get("summary", {}).get("calibrated", False),
                    )
                else:
                    logger.warning(
                        "job_id=%s body_language_metrics returned None — "
                        "video may be unreadable (codec issue?) or has no frames",
                        job_id,
                    )
                return bl
            except Exception:
                logger.error("job_id=%s body_language_metrics_failed", job_id, exc_info=True)
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            tone_future: Future = pool.submit(_compute_tone_metrics)
            body_future: Future = pool.submit(_compute_body_language)
            tone_result = tone_future.result()
            body_language = body_future.result()

        derived_metrics.update(tone_result)
        if body_language is not None:
            derived_metrics["body_language"] = body_language

        # Ensure video upload finished (non-blocking wait)
        try:
            video_future.result(timeout=30)
        except Exception:
            logger.warning("job_id=%s video_future_timeout_or_error", job_id, exc_info=True)

        job_store.update_job(job_id, progress=90)
        try:
            artifacts_gcs_prefix, artifact_has_diarization = write_transcript_artifacts(
                job_id=job_id,
                bucket_name=bucket_name,
                transcript_result=transcript_result,
                model=str(stt_payload.get("model") or "chirp_2"),
                location=str(stt_payload.get("location") or "us-central1"),
                diarization_requested=bool(stt_payload.get("diarization_requested", True)),
            )
            has_diarization = artifact_has_diarization
        except Exception as artifact_exc:
            artifacts_error = str(artifact_exc)
            logger.warning(
                "job_id=%s artifact_upload_failed error=%s",
                job_id,
                artifacts_error,
            )

        job_store.update_job(
            job_id,
            status="done",
            progress=100,
            result=transcript_result,
            transcript_full_text=transcript_full_text,
            transcript_words=transcript_words,
            transcript_segments=transcript_segments,
            derived_metrics=derived_metrics,
            artifacts_gcs_prefix=artifacts_gcs_prefix,
            has_diarization=has_diarization,
            artifacts_error=artifacts_error,
            error=None,
        )

        logger.info(
            "job_id=%s transcript_done full_text_len=%s words=%s",
            job_id,
            len(transcript_result.get("full_text", "")),
            len(transcript_result.get("words", [])),
        )
    except Exception as exc:
        job_store.update_job(job_id, status="failed", progress=100, error=str(exc))
    finally:
        cleanup_audio = parse_bool_env("GCS_CLEANUP_AUDIO", True)
        cleanup_output = parse_bool_env("GCS_CLEANUP_OUTPUT", True)

        if bucket_name:
            if cleanup_output:
                try:
                    delete_prefix(output_prefix, bucket=bucket_name)
                except Exception:
                    logger.warning(
                        "job_id=%s cleanup_output_failed prefix=gs://%s/%s",
                        job_id,
                        bucket_name,
                        output_prefix,
                        exc_info=True,
                    )
            if cleanup_audio and uploaded_audio:
                try:
                    delete_blob(bucket_name, audio_blob_path)
                except Exception:
                    logger.warning(
                        "job_id=%s cleanup_audio_failed uri=gs://%s/%s",
                        job_id,
                        bucket_name,
                        audio_blob_path,
                        exc_info=True,
                    )

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
