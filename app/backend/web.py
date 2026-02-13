import json
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .constants import MAX_REQUEST_BYTES, MAX_UPLOAD_BYTES
from .coaching_round1 import run_round1
from .coaching_round2 import run_round2
from .coaching_round3 import run_round3
from .coaching_round4 import run_round4
from .deck_extractor import (
    detect_extension,
    sanitize_filename,
    validate_deck_extension,
)
from .llm_client import run_llm_test_prompt
from .models import (
    CreateJobResponse,
    JobStatusResponse,
    LLMTestResponse,
    Round1FeedbackResponse,
    Round2FeedbackResponse,
    Round3FeedbackResponse,
    Round4FeedbackResponse,
    SummarizeResponse,
)
from .summarization import process_summary_job
from .storage import build_job_store
from .transcription import process_deck_only_job, process_transcription_job, write_upload_to_disk


logger = logging.getLogger("uvicorn.error")

DECK_STORAGE_ROOT = Path(os.getenv("DECK_STORAGE_DIR", "data/decks")).resolve()
ALLOWED_MIME_BY_EXTENSION = {
    ".pdf": {
        "application/pdf",
        "application/x-pdf",
        "application/octet-stream",
    },
    ".pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
        "application/octet-stream",
    },
    ".ppt": {"application/vnd.ms-powerpoint", "application/octet-stream"},
}

app = FastAPI(title="AI Pitching Coach Backend")
job_store = build_job_store()

# Transient storage for upload temp paths (between upload and process calls).
# Not persisted — only valid within the same dyno lifecycle.
_upload_temp_paths: dict[str, dict] = {}

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
    if request.method in ("POST", "PUT") and request.url.path.startswith("/api/jobs"):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"Request too large. Max size is {MAX_REQUEST_BYTES} bytes."},
                    )
            except ValueError:
                pass
    return await call_next(request)


def _validate_deck_mime(content_type: Optional[str], extension: str) -> None:
    if not content_type:
        return
    allowed = ALLOWED_MIME_BY_EXTENSION.get(extension, set())
    if content_type not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid deck content type for {extension}: {content_type}",
        )


async def _save_deck_upload(job_id: str, deck: UploadFile) -> dict:
    raw_name = deck.filename or "deck"
    extension = detect_extension(raw_name)
    try:
        validate_deck_extension(extension)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _validate_deck_mime(deck.content_type, extension)

    safe_filename = sanitize_filename(raw_name)
    deck_dir = DECK_STORAGE_ROOT / job_id
    deck_path = deck_dir / safe_filename

    try:
        size_bytes = await write_upload_to_disk(
            deck,
            deck_path,
            field_name="deck",
            max_size_bytes=MAX_UPLOAD_BYTES,
        )
    except Exception:
        _cleanup_deck_file(str(deck_path))
        raise

    return {
        "filename": safe_filename,
        "content_type": deck.content_type,
        "size_bytes": size_bytes,
        "storage_path": str(deck_path),
    }


def _cleanup_deck_file(storage_path: Optional[str]) -> None:
    if not storage_path:
        return

    path = Path(storage_path)
    try:
        if path.exists():
            path.unlink()
        if path.parent.exists() and not any(path.parent.iterdir()):
            path.parent.rmdir()
    except Exception:
        logger.warning("Failed to cleanup deck path=%s", storage_path, exc_info=True)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "storage": job_store.storage_name}


@app.post("/api/jobs", response_model=CreateJobResponse)
async def create_transcription_job(
    background_tasks: BackgroundTasks,
    video: Optional[UploadFile] = File(None),
    deck: Optional[UploadFile] = File(None),
) -> CreateJobResponse:
    if video is None:
        raise HTTPException(status_code=400, detail="Missing video file.")

    job_id = str(uuid.uuid4())
    job_store.create_job(job_id)

    temp_dir = Path(tempfile.mkdtemp(prefix=f"job_{job_id}_"))
    suffix = Path(video.filename or "").suffix or ".webm"
    input_path = temp_dir / f"input{suffix}"
    deck_upload = None

    try:
        await write_upload_to_disk(video, input_path, field_name="video", max_size_bytes=MAX_UPLOAD_BYTES)
        if deck is not None:
            deck_upload = await _save_deck_upload(job_id, deck)
    except Exception:
        job_store.delete_job(job_id)
        shutil.rmtree(temp_dir, ignore_errors=True)
        _cleanup_deck_file(deck_upload["storage_path"] if deck_upload else None)
        raise

    background_tasks.add_task(
        process_transcription_job,
        job_store,
        job_id,
        input_path,
        temp_dir,
        deck_upload,
    )
    return CreateJobResponse(job_id=job_id, status="queued")


@app.post("/api/jobs/prepare", response_model=CreateJobResponse)
def prepare_job() -> CreateJobResponse:
    """Create a job shell so the client can upload video in a separate request.
    Splitting prepare + upload lets us keep each request's data flowing
    continuously, which avoids Heroku's H28 idle-connection timeout."""
    job_id = str(uuid.uuid4())
    job_store.create_job(job_id)
    logger.info("job_id=%s prepare_job created", job_id)
    return CreateJobResponse(job_id=job_id, status="created")


@app.put("/api/jobs/{job_id}/upload-video")
async def upload_video_streaming(
    job_id: str,
    request: Request,
) -> StreamingResponse:
    """Receive raw video bytes and stream NDJSON progress lines back.

    The client sends the video as a raw binary PUT body (not multipart).
    The server reads chunks, writes them to disk, and sends a progress
    JSON line after every chunk.  This keeps data flowing in BOTH
    directions, preventing Heroku's H28 (Client Connection Idle) timeout.

    On success the last line is: {"status":"done","bytes":<total>}
    On error:  {"status":"error","detail":"..."}
    """
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    temp_dir = Path(tempfile.mkdtemp(prefix=f"job_{job_id}_"))
    input_path = temp_dir / "input.webm"

    async def _stream_progress():
        total_bytes = 0
        try:
            input_path.parent.mkdir(parents=True, exist_ok=True)
            with input_path.open("wb") as f:
                async for chunk in request.stream():
                    f.write(chunk)
                    total_bytes += len(chunk)
                    if total_bytes > MAX_UPLOAD_BYTES:
                        yield json.dumps({"status": "error", "detail": "Video too large."}) + "\n"
                        return
                    # Send progress line back — keeps Heroku connection alive
                    yield json.dumps({"status": "uploading", "bytes": total_bytes}) + "\n"

            if total_bytes == 0:
                yield json.dumps({"status": "error", "detail": "Empty video upload."}) + "\n"
                return

            # Stash the temp paths for the /process call
            _upload_temp_paths[job_id] = {
                "temp_dir": str(temp_dir),
                "input_path": str(input_path),
            }
            yield json.dumps({"status": "done", "bytes": total_bytes}) + "\n"
            logger.info("job_id=%s upload_video_streaming bytes=%d", job_id, total_bytes)
        except Exception as exc:
            logger.exception("job_id=%s upload_video_streaming error", job_id)
            yield json.dumps({"status": "error", "detail": str(exc)}) + "\n"

    return StreamingResponse(
        _stream_progress(),
        media_type="application/x-ndjson",
    )


@app.post("/api/jobs/{job_id}/process", response_model=CreateJobResponse)
async def start_processing(
    job_id: str,
    background_tasks: BackgroundTasks,
    deck: Optional[UploadFile] = File(None),
) -> CreateJobResponse:
    """Kick off transcription after the video has been uploaded via the
    streaming endpoint.  Optionally attach a deck (small enough for Heroku)."""
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status not in ("queued", "pending", "created", "failed"):
        raise HTTPException(
            status_code=400,
            detail=f"Job is already being processed (status={job.status}).",
        )

    # Retrieve the paths saved by the upload endpoint
    paths = _upload_temp_paths.pop(job_id, None)
    if not paths:
        raise HTTPException(
            status_code=400,
            detail="Video has not been uploaded yet. Call PUT /upload-video first.",
        )
    temp_dir = Path(paths["temp_dir"])
    input_path = Path(paths["input_path"])
    if not input_path.exists():
        raise HTTPException(
            status_code=400,
            detail="Uploaded video file not found on disk. Please re-upload.",
        )

    deck_upload = None
    try:
        if deck is not None:
            deck_upload = await _save_deck_upload(job_id, deck)
    except Exception:
        _cleanup_deck_file(deck_upload["storage_path"] if deck_upload else None)
        raise

    background_tasks.add_task(
        process_transcription_job,
        job_store,
        job_id,
        input_path,
        temp_dir,
        deck_upload,
    )
    return CreateJobResponse(job_id=job_id, status="queued")


@app.post("/api/jobs/{job_id}/deck", response_model=CreateJobResponse)
async def attach_deck_to_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    deck: Optional[UploadFile] = File(None),
) -> CreateJobResponse:
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if deck is None:
        raise HTTPException(status_code=400, detail="Missing deck file.")

    deck_upload = await _save_deck_upload(job_id, deck)
    background_tasks.add_task(process_deck_only_job, job_store, job_id, deck_upload)
    return CreateJobResponse(job_id=job_id, status="deck_processing")


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str) -> JobStatusResponse:
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobStatusResponse(
        job_id=job_id,
        status=job.status,
        progress=job.progress,
        transcript=job.result,
        deck=job.deck,
        llm_test_output=job.llm_test_output,
        summary=job.summary_json,
        summary_error=job.summary_error,
        derived_metrics=job.derived_metrics,
        feedback_round_1_status=job.feedback_round_1_status,
        feedback_round_1=job.feedback_round_1,
        feedback_round_1_version=job.feedback_round_1_version,
        feedback_round_1_error=job.feedback_round_1_error,
        feedback_round_2_status=job.feedback_round_2_status,
        feedback_round_2=job.feedback_round_2,
        feedback_round_2_version=job.feedback_round_2_version,
        feedback_round_2_error=job.feedback_round_2_error,
        feedback_round_3_status=job.feedback_round_3_status,
        feedback_round_3=job.feedback_round_3,
        feedback_round_3_version=job.feedback_round_3_version,
        feedback_round_3_error=job.feedback_round_3_error,
        feedback_round_4_status=job.feedback_round_4_status,
        feedback_round_4=job.feedback_round_4,
        feedback_round_4_version=job.feedback_round_4_version,
        feedback_round_4_error=job.feedback_round_4_error,
        result=job.result,
        video_gcs_uri=job.video_gcs_uri,
        error=job.error,
    )


@app.post("/api/jobs/{job_id}/summarize", response_model=SummarizeResponse)
def summarize_job(job_id: str, background_tasks: BackgroundTasks) -> SummarizeResponse:
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    transcript_payload = job.result if isinstance(job.result, dict) else {}
    transcript_text = str(transcript_payload.get("full_text") or "").strip()
    if not transcript_text:
        raise HTTPException(
            status_code=400,
            detail="Transcript is missing for this job. Wait for transcription to finish first.",
        )

    job_store.update_job(
        job_id,
        status="summarizing",
        progress=70,
        summary_json=None,
        summary_error=None,
        error=None,
    )
    background_tasks.add_task(process_summary_job, job_store, job_id)
    return SummarizeResponse(job_id=job_id, status="summarizing")


@app.post("/api/jobs/{job_id}/feedback/round1", response_model=Round1FeedbackResponse)
def generate_round1_feedback(job_id: str, background_tasks: BackgroundTasks) -> Round1FeedbackResponse:
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    transcript_payload = job.result if isinstance(job.result, dict) else {}
    transcript_text = str(
        job.transcript_full_text or transcript_payload.get("full_text") or ""
    ).strip()
    if not transcript_text:
        raise HTTPException(
            status_code=400,
            detail="Transcript is missing for this job. Wait for transcription to finish first.",
        )

    if job.feedback_round_1_status == "done" and isinstance(job.feedback_round_1, dict):
        return Round1FeedbackResponse(job_id=job_id, status="done")
    if job.feedback_round_1_status == "running":
        return Round1FeedbackResponse(job_id=job_id, status="running")

    job_store.update_job(
        job_id,
        feedback_round_1_status="running",
        feedback_round_1_error=None,
        feedback_round_1_version="r1_v1",
    )
    background_tasks.add_task(run_round1, job_store, job_id)
    return Round1FeedbackResponse(job_id=job_id, status="running")


@app.post("/api/jobs/{job_id}/feedback/round2", response_model=Round2FeedbackResponse)
def generate_round2_feedback(job_id: str, background_tasks: BackgroundTasks) -> Round2FeedbackResponse:
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    transcript_payload = job.result if isinstance(job.result, dict) else {}
    transcript_text = str(
        job.transcript_full_text or transcript_payload.get("full_text") or ""
    ).strip()
    if not transcript_text:
        raise HTTPException(
            status_code=400,
            detail="Transcript is missing for this job. Wait for transcription to finish first.",
        )

    if job.feedback_round_2_status == "done" and isinstance(job.feedback_round_2, dict):
        return Round2FeedbackResponse(job_id=job_id, status="done")
    if job.feedback_round_2_status == "running":
        return Round2FeedbackResponse(job_id=job_id, status="running")

    job_store.update_job(
        job_id,
        feedback_round_2_status="running",
        feedback_round_2_error=None,
        feedback_round_2_version="r2_v1",
    )
    background_tasks.add_task(run_round2, job_store, job_id)
    return Round2FeedbackResponse(job_id=job_id, status="running")


@app.post("/api/jobs/{job_id}/feedback/round3", response_model=Round3FeedbackResponse)
def generate_round3_feedback(job_id: str, background_tasks: BackgroundTasks) -> Round3FeedbackResponse:
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    transcript_payload = job.result if isinstance(job.result, dict) else {}
    transcript_text = str(
        job.transcript_full_text or transcript_payload.get("full_text") or ""
    ).strip()
    if not transcript_text:
        raise HTTPException(
            status_code=400,
            detail="Transcript is missing for this job. Wait for transcription to finish first.",
        )

    if job.feedback_round_3_status == "done" and isinstance(job.feedback_round_3, dict):
        return Round3FeedbackResponse(job_id=job_id, status="done")
    if job.feedback_round_3_status == "running":
        return Round3FeedbackResponse(job_id=job_id, status="running")

    job_store.update_job(
        job_id,
        feedback_round_3_status="running",
        feedback_round_3_error=None,
        feedback_round_3_version="r3_v1",
    )
    background_tasks.add_task(run_round3, job_store, job_id)
    return Round3FeedbackResponse(job_id=job_id, status="running")


@app.post("/api/jobs/{job_id}/feedback/round4", response_model=Round4FeedbackResponse)
def generate_round4_feedback(job_id: str, background_tasks: BackgroundTasks) -> Round4FeedbackResponse:
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    transcript_payload = job.result if isinstance(job.result, dict) else {}
    transcript_text = str(
        job.transcript_full_text or transcript_payload.get("full_text") or ""
    ).strip()
    if not transcript_text:
        raise HTTPException(
            status_code=400,
            detail="Transcript is missing for this job. Wait for transcription to finish first.",
        )

    if job.feedback_round_4_status == "done" and isinstance(job.feedback_round_4, dict):
        return Round4FeedbackResponse(job_id=job_id, status="done")
    if job.feedback_round_4_status == "running":
        return Round4FeedbackResponse(job_id=job_id, status="running")

    job_store.update_job(
        job_id,
        feedback_round_4_status="running",
        feedback_round_4_error=None,
        feedback_round_4_version="r4_v1",
    )
    background_tasks.add_task(run_round4, job_store, job_id)
    return Round4FeedbackResponse(job_id=job_id, status="running")


@app.post("/api/jobs/{job_id}/llm_test", response_model=LLMTestResponse)
def run_llm_test(job_id: str) -> LLMTestResponse:
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    transcript_payload = job.result if isinstance(job.result, dict) else {}
    transcript_text = str(transcript_payload.get("full_text") or "").strip()
    if not transcript_text:
        raise HTTPException(
            status_code=400,
            detail="Transcript is missing for this job. Wait for transcription to finish first.",
        )

    try:
        llm_test_output = run_llm_test_prompt(transcript_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        detail = str(exc)
        status_code = 500 if "GPTSAPI_KEY" in detail else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc

    job_store.update_job(job_id, llm_test_output=llm_test_output)
    return LLMTestResponse(job_id=job_id, status="done", llm_test_output=llm_test_output)


frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response: Response = await call_next(request)
    path = request.url.path
    if path.endswith((".js", ".css", ".html")) or path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response
