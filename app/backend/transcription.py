import logging
import shutil
import subprocess
import wave
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
MAX_SYNC_RECOGNIZE_SECONDS = 55.0


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


def get_wav_duration_seconds(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as source:
        frame_rate = source.getframerate()
        frame_count = source.getnframes()
        if frame_rate <= 0:
            return 0.0
        return frame_count / float(frame_rate)


def split_wav_into_sync_chunks(
    wav_path: Path, *, max_chunk_seconds: float = MAX_SYNC_RECOGNIZE_SECONDS
) -> list[tuple[Path, float]]:
    if max_chunk_seconds <= 0:
        raise ValueError("max_chunk_seconds must be positive.")

    chunks: list[tuple[Path, float]] = []
    with wave.open(str(wav_path), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        frame_rate = source.getframerate()
        bytes_per_frame = channels * sample_width
        if frame_rate <= 0 or bytes_per_frame <= 0:
            raise RuntimeError("Invalid WAV metadata after conversion.")

        frames_per_chunk = max(1, int(max_chunk_seconds * frame_rate))
        frames_consumed = 0
        chunk_index = 0

        while True:
            chunk_bytes = source.readframes(frames_per_chunk)
            if not chunk_bytes:
                break

            frames_in_chunk = len(chunk_bytes) // bytes_per_frame
            if frames_in_chunk <= 0:
                break

            offset_seconds = frames_consumed / float(frame_rate)
            chunk_path = wav_path.parent / f"chunk_{chunk_index:03d}.wav"

            with wave.open(str(chunk_path), "wb") as chunk_writer:
                chunk_writer.setnchannels(channels)
                chunk_writer.setsampwidth(sample_width)
                chunk_writer.setframerate(frame_rate)
                chunk_writer.writeframes(chunk_bytes)

            chunks.append((chunk_path, offset_seconds))
            frames_consumed += frames_in_chunk
            chunk_index += 1

    return chunks


def apply_time_offset_to_transcript(transcript: dict, offset_seconds: float) -> dict:
    if offset_seconds <= 0:
        return transcript

    shifted_segments: list[dict] = []
    for segment in transcript.get("segments", []):
        shifted_segments.append(
            {
                "start": float(segment.get("start", 0.0) or 0.0) + offset_seconds,
                "end": float(segment.get("end", 0.0) or 0.0) + offset_seconds,
                "text": segment.get("text", ""),
            }
        )

    shifted_words: list[dict] = []
    for word in transcript.get("words", []):
        shifted_words.append(
            {
                "start": float(word.get("start", 0.0) or 0.0) + offset_seconds,
                "end": float(word.get("end", 0.0) or 0.0) + offset_seconds,
                "word": word.get("word", ""),
            }
        )

    return {
        "full_text": transcript.get("full_text", ""),
        "segments": shifted_segments,
        "words": shifted_words,
    }


def merge_transcript_chunks(chunks: list[dict]) -> dict:
    full_text_parts: list[str] = []
    all_segments: list[dict] = []
    all_words: list[dict] = []

    for chunk in chunks:
        chunk_text = str(chunk.get("full_text") or "").strip()
        if chunk_text:
            full_text_parts.append(chunk_text)
        all_segments.extend(chunk.get("segments", []))
        all_words.extend(chunk.get("words", []))

    return {
        "full_text": " ".join(full_text_parts).strip(),
        "segments": all_segments,
        "words": all_words,
    }


def transcribe_wav_with_sync_chunking(
    *,
    client: speech.SpeechClient,
    config: speech.RecognitionConfig,
    wav_path: Path,
    job_store: JobStore,
    job_id: str,
) -> dict:
    duration_seconds = get_wav_duration_seconds(wav_path)
    if duration_seconds <= MAX_SYNC_RECOGNIZE_SECONDS:
        with wav_path.open("rb") as wav_file:
            wav_content = wav_file.read()
        if not wav_content:
            raise RuntimeError("Converted WAV audio is empty.")

        response = client.recognize(config=config, audio=speech.RecognitionAudio(content=wav_content))
        job_store.update_job(job_id, progress=85)
        return parse_speech_response(response)

    chunk_paths = split_wav_into_sync_chunks(wav_path, max_chunk_seconds=MAX_SYNC_RECOGNIZE_SECONDS)
    if not chunk_paths:
        raise RuntimeError("Failed to split long recording into STT chunks.")

    logger.info(
        "job_id=%s long_audio_detected duration_seconds=%.2f chunk_count=%s",
        job_id,
        duration_seconds,
        len(chunk_paths),
    )

    chunk_results: list[dict] = []
    total_chunks = len(chunk_paths)

    for index, (chunk_path, offset_seconds) in enumerate(chunk_paths):
        with chunk_path.open("rb") as chunk_file:
            chunk_bytes = chunk_file.read()
        if not chunk_bytes:
            continue

        response = client.recognize(
            config=config,
            audio=speech.RecognitionAudio(content=chunk_bytes),
        )
        parsed = parse_speech_response(response)
        chunk_results.append(apply_time_offset_to_transcript(parsed, offset_seconds))

        # Keep polling UI responsive while chunking long recordings.
        progress = 60 + int(((index + 1) / total_chunks) * 30)
        job_store.update_job(job_id, progress=min(progress, 89))

    return merge_transcript_chunks(chunk_results)


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

        client = build_speech_client()
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code="en-US",
            enable_automatic_punctuation=True,
            enable_word_time_offsets=True,
        )
        result = transcribe_wav_with_sync_chunking(
            client=client,
            config=config,
            wav_path=wav_path,
            job_store=job_store,
            job_id=job_id,
        )
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
