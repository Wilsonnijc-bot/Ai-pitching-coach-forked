# AI Pitching Coach Backend

Backend flow:

`Record -> Stop -> Upload audio (+ optional deck) -> Async job -> Poll result`

## Stack

- FastAPI
- Google Cloud Speech-to-Text V2 BatchRecognize (`chirp_2`)
- Google Cloud Storage (input/output for batch recognition)
- ffmpeg (audio -> WAV 16kHz mono)
- PostgreSQL (when `DATABASE_URL` is set) or in-memory fallback
- Deck extraction:
  - PDF: `pypdf`
  - PPTX: `python-pptx`
  - PPT: rejected for MVP with clear error

## Backend Modules

- `app/backend/web.py`: routes, CORS, upload size middleware, static frontend mount.
- `app/backend/transcription.py`: file writes, deck processing, ffmpeg conversion, V2 STT pipeline.
- `app/backend/deck_extractor.py`: deck parsing and text extraction.
- `app/backend/gcs_utils.py`: GCS upload/download/list/delete helpers.
- `app/backend/stt_v2.py`: Speech-to-Text V2 Chirp 2 batch request + output parsing + speaker mapping.
- `app/backend/llm_client.py`: GPTsAPI/OpenAI-compatible client for `llm_test`.
- `app/backend/llm_gptsapi.py`: raw HTTP GPTsAPI client for async summary generation.
- `app/backend/summarization.py`: background summary pipeline + JSON validation/repair retry.
- `app/backend/storage.py`: job/deck persistence (memory + PostgreSQL).
- `app/backend/models.py`: API models.
- `app/backend/constants.py`: limits/chunk sizes.
- `main.py`: app entrypoint.

## API Contract

### 1) Create Job

`POST /api/jobs` (multipart/form-data)

- `audio` (required)
- `deck` (optional, `.pdf` or `.pptx`; `.ppt` rejected)

Response (immediate):

```json
{
  "job_id": "<uuid>",
  "status": "queued"
}
```

### 2) Poll Job

`GET /api/jobs/{job_id}`

Response:

```json
{
  "job_id": "<uuid>",
  "status": "queued|deck_processing|transcribing|uploading_audio_to_gcs|stt_batch_recognize|waiting_for_stt|parsing_results|summarizing|done|failed",
  "progress": 0,
  "transcript": {
    "full_text": "",
    "segments": [],
    "words": []
  },
  "deck": {
    "filename": "deck.pdf",
    "content_type": "application/pdf",
    "size_bytes": 12345,
    "text_excerpt": "first 500 chars",
    "num_pages_or_slides": 10
  },
  "llm_test_output": null,
  "summary": null,
  "summary_error": null,
  "result": null,
  "error": null
}
```

`result` is kept as a backward-compatible alias of `transcript`.

### Optional deck attach endpoint

`POST /api/jobs/{job_id}/deck` with multipart field `deck` is also supported for compatibility.

### 3) LLM Test Endpoint

`POST /api/jobs/{job_id}/llm_test`

Behavior:

- Reads `transcript.full_text` from the stored job.
- Calls GPTsAPI using:
  - system: `You are a helpful assistant.`
  - user: `Here is a pitch transcript. Summarize it in 5 bullet points, in English: ...`
- Stores raw model output to `llm_test_output`.

Response:

```json
{
  "job_id": "<uuid>",
  "status": "done",
  "llm_test_output": "- bullet 1\n- bullet 2\n..."
}
```

### 4) Async Summary Endpoint

`POST /api/jobs/{job_id}/summarize`

Immediate response:

```json
{
  "job_id": "<uuid>",
  "status": "summarizing"
}
```

Background behavior:

- Reads transcript `full_text` (and deck extracted text if present)
- Calls GPTsAPI model `gpt-5.1-chat`
- Enforces JSON output schema and stores parsed JSON into `summary`
- On parse failure, runs one repair retry
- On repeated failure, marks job `failed` with `summary_error`

Required summary schema:

```json
{
  "title": "string",
  "one_sentence_summary": "string",
  "key_points": ["string"],
  "audience": "string",
  "ask_or_goal": "string",
  "clarity_score": 1,
  "confidence": "low",
  "red_flags": ["string"],
  "next_steps": ["string"]
}
```

## Job Processing States

- `queued` -> initial row created
- `deck_processing` -> optional deck extraction and DB save (`progress=10`)
- `transcribing` -> local ffmpeg conversion stage before GCS upload (`progress=10`)
- `uploading_audio_to_gcs` -> converted WAV uploaded to `gs://<bucket>/jobs/<job_id>/audio.wav` (`progress=20`)
- `stt_batch_recognize` -> V2 BatchRecognize request submitted (`progress=40`)
- `waiting_for_stt` -> waiting for long-running operation (`progress=60`)
- `parsing_results` -> reading/parsing V2 JSON output from GCS (`progress=80`)
- `summarizing` -> GPTsAPI summary generation (`progress=70..90`)
- `done` -> transcript + optional deck ready (`progress=100`)
- `failed` -> error stored in DB

## Persistence

If `DATABASE_URL` is set, backend auto-creates:

- `transcription_jobs`
- `deck_assets` (1:1 by `job_id`, FK to `transcription_jobs`)

`deck_assets` stores metadata + extracted text/JSON, not raw bytes.
`transcription_jobs` also stores `llm_test_output` (TEXT, nullable).
`transcription_jobs` also stores `summary_json` (JSONB, nullable) and `summary_error` (TEXT, nullable).
`transcription_jobs` also stores:
- `artifacts_gcs_prefix` (TEXT, nullable)
- `has_diarization` (BOOLEAN, nullable)
- `artifacts_error` (TEXT, nullable)

For existing databases, schema update is automatic on startup:

```sql
ALTER TABLE transcription_jobs ADD COLUMN IF NOT EXISTS llm_test_output TEXT NULL;
ALTER TABLE transcription_jobs ADD COLUMN IF NOT EXISTS summary_json JSONB NULL;
ALTER TABLE transcription_jobs ADD COLUMN IF NOT EXISTS summary_error TEXT NULL;
ALTER TABLE transcription_jobs ADD COLUMN IF NOT EXISTS artifacts_gcs_prefix TEXT NULL;
ALTER TABLE transcription_jobs ADD COLUMN IF NOT EXISTS has_diarization BOOLEAN NULL;
ALTER TABLE transcription_jobs ADD COLUMN IF NOT EXISTS artifacts_error TEXT NULL;
```

If `DATABASE_URL` is missing, in-memory storage is used (reset on restart).

## Local File Storage

- Deck files are saved to: `data/decks/<job_id>/<sanitized_filename>`
- Audio temp files are written to temp dirs and cleaned after processing.
- STT V2 GCS objects:
  - Input audio: `gs://<GCS_AUDIO_BUCKET>/jobs/<job_id>/audio.wav`
  - Output JSON: `gs://<GCS_AUDIO_BUCKET>/jobs/<job_id>/stt_v2_output/`
  - Retained artifacts: `gs://<GCS_AUDIO_BUCKET>/jobs/<job_id>/artifacts/`
    - `transcript.txt` (normalized full transcript text)
    - `words.json` (word-level timestamps, includes `speaker` when present)
    - `diarization.json` (speaker grouped turns + availability flag; emits a note if diarization is unavailable)
    - `meta.json` (engine/model/location + artifact URIs + feature flags, including diarization requested/available)
- Cleanup flags:
  - `GCS_CLEANUP_AUDIO=true` (default)
  - `GCS_CLEANUP_OUTPUT=true` (default)
- `artifacts/` is retained (never auto-deleted by cleanup flags).

If Chirp 2 / API rejects diarization config, backend retries without diarization and still completes the job.
- Deck files are currently retained for debugging/replay in MVP; clear `data/decks/` as needed.

## Limits

- Per-file max: `25MB` (`audio` and `deck`)
- Request max: `60MB`
- CORS default origins:
  - `http://localhost:5173`
  - `http://127.0.0.1:5173`

Override via `FRONTEND_ORIGINS` (comma-separated).

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
brew install ffmpeg
export GOOGLE_APPLICATION_CREDENTIALS="/Users/nijiachen/Downloads/double-scholar-487115-b1-f20d293ef8d3.json"
export GCP_PROJECT_ID="double-scholar-487115-b1"
export GCP_SPEECH_LOCATION="us-central1"
export GCS_AUDIO_BUCKET="audiosss1"
# Optional cleanup toggles:
export GCS_CLEANUP_AUDIO="true"
export GCS_CLEANUP_OUTPUT="true"
export GPTSAPI_KEY="YOUR_KEY_HERE"
# Optional overrides:
export GPTSAPI_BASE_URL="https://api.gptsapi.net/v1"
export GPTSAPI_MODEL="gpt-5.1-chat"
uvicorn main:app --reload --port 8000
```

## Curl Tests

Create job with audio only:

```bash
curl -F "audio=@/path/to/sample.webm" http://127.0.0.1:8000/api/jobs
```

For STT V2 validation, use a sample longer than 60 seconds (for example ~2 minutes). V2 BatchRecognize uses GCS and does not require local chunking.

Create job with audio + deck:

```bash
curl \
  -F "audio=@/path/to/sample.webm" \
  -F "deck=@/path/to/deck.pdf" \
  http://127.0.0.1:8000/api/jobs
```

Poll result:

```bash
curl http://127.0.0.1:8000/api/jobs/<job_id>
```

Run LLM test on a completed job:

```bash
curl -X POST http://127.0.0.1:8000/api/jobs/<job_id>/llm_test
```

Run async summary on a completed job:

```bash
curl -X POST http://127.0.0.1:8000/api/jobs/<job_id>/summarize
```

Poll until `status` is `done` (or `failed`) and inspect `summary` / `summary_error`:

```bash
curl http://127.0.0.1:8000/api/jobs/<job_id>
```

## Credentials (Backend Only)

Use one of:

- `GOOGLE_APPLICATION_CREDENTIALS`
- `GOOGLE_APPLICATION_CREDENTIALS_JSON`
- `GOOGLE_APPLICATION_CREDENTIALS_B64`
- `GCP_PROJECT_ID` (default: `double-scholar-487115-b1`)
- `GCP_SPEECH_LOCATION` (default: `us-central1`)
- `GCS_AUDIO_BUCKET` (default: `audiosss1`)
- `GCS_CLEANUP_AUDIO` (optional, default `true`)
- `GCS_CLEANUP_OUTPUT` (optional, default `true`)
- `GPTSAPI_KEY` (required for `/api/jobs/{job_id}/llm_test` and `/api/jobs/{job_id}/summarize`)
- `GPTSAPI_BASE_URL` (optional; default `https://api.gptsapi.net/v1`)
- `GPTSAPI_MODEL` (optional; default `gpt-5.1-chat`)
- `GPTSAPI_AUTH_MODE` (optional; `authorization` default, `x-api-key` supported)

Never send Google credentials to frontend code.

## Optional GCS Diagnostic

Use this script to verify Storage access quickly:

```bash
python scripts/diag_gcs.py
```

It uploads a temporary text object, reads it back, then deletes it.

## Artifact Integration Test

With backend running locally, verify transcript artifact persistence:

```bash
python scripts/test_stt_artifacts.py --audio /path/to/sample.webm
```

The script creates a job, polls until completion, then checks:
- `transcript.txt`
- `words.json`
- `diarization.json`
- `meta.json`
