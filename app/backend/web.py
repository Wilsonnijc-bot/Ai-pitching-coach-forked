import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .constants import MAX_UPLOAD_BYTES
from .models import CreateJobResponse, JobStatusResponse
from .storage import build_job_store
from .transcription import process_transcription_job, write_upload_to_disk


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
        job_store.delete_job(job_id)
        raise

    background_tasks.add_task(process_transcription_job, job_store, job_id, input_path, temp_dir)
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


frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
