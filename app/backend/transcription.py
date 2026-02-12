import shutil
import subprocess
from pathlib import Path

from fastapi import HTTPException, UploadFile
from google.api_core.exceptions import GoogleAPICallError
from google.cloud import speech

from .constants import CHUNK_SIZE, MAX_UPLOAD_BYTES
from .google_stt import build_speech_client, parse_speech_response
from .storage import JobStore


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


def process_transcription_job(
    job_store: JobStore,
    job_id: str,
    input_path: Path,
    temp_dir: Path,
) -> None:
    try:
        job_store.update_job(job_id, status="transcribing", progress=20, error=None)

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

        job_store.update_job(job_id, progress=90)
        result = parse_speech_response(response)
        job_store.update_job(job_id, status="done", progress=100, result=result, error=None)
    except GoogleAPICallError as exc:
        job_store.update_job(
            job_id, status="failed", progress=100, error=f"Google Speech-to-Text error: {exc}"
        )
    except Exception as exc:
        job_store.update_job(job_id, status="failed", progress=100, error=str(exc))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
