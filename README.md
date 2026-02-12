# AI Pitching Coach Backend

Backend for:

`Record -> Stop -> Upload -> Transcribe -> Show results`

## What It Uses

- FastAPI
- ffmpeg (convert uploaded audio to WAV 16kHz mono)
- Google Cloud Speech-to-Text V1
- Async job API (`POST /api/jobs` + polling)
- Storage:
  - `DATABASE_URL` set -> PostgreSQL
  - `DATABASE_URL` missing -> in-memory (local fallback)

## API Contract

### Create Transcription Job

`POST /api/jobs`  
Content-Type: `multipart/form-data`  
Field:
- `audio` (required)

Response:

```json
{
  "job_id": "<uuid>",
  "status": "queued"
}
```

### Poll Job Status / Result

`GET /api/jobs/{job_id}`

Response:

```json
{
  "job_id": "<uuid>",
  "status": "queued | transcribing | done | failed",
  "progress": 0,
  "result": null,
  "error": null
}
```

Success result shape:

```json
{
  "full_text": "string",
  "segments": [{"start": 0.0, "end": 1.2, "text": "hello"}],
  "words": [{"start": 0.0, "end": 0.4, "word": "hello"}]
}
```

## Local Setup

1. Create and activate venv:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install deps:

```bash
pip install -r requirements.txt
```

3. Install ffmpeg:

```bash
brew install ffmpeg
```

4. Set Google credentials (local file):

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/Users/nijiachen/Downloads/double-scholar-487115-b1-f20d293ef8d3.json"
```

5. Run:

```bash
uvicorn main:app --reload --port 8000
```

## Credential Options (Backend Only)

Use one of:

- `GOOGLE_APPLICATION_CREDENTIALS` (path to JSON file)
- `GOOGLE_APPLICATION_CREDENTIALS_JSON` (full JSON string)
- `GOOGLE_APPLICATION_CREDENTIALS_B64` (base64-encoded JSON)

Never expose these to frontend code.

## Heroku Deployment

This repo includes:

- `Procfile` for web process
- `Aptfile` with `ffmpeg`

Set buildpacks:

```bash
heroku buildpacks:clear -a ai-pitching-coach
heroku buildpacks:add --index 1 heroku-community/apt -a ai-pitching-coach
heroku buildpacks:add --index 2 heroku/python -a ai-pitching-coach
```

Add PostgreSQL:

```bash
heroku addons:create heroku-postgresql:essential-0 -a ai-pitching-coach
```

Set CORS + credential vars:

```bash
heroku config:set FRONTEND_ORIGINS="https://your-frontend-domain.com,http://localhost:5173" -a ai-pitching-coach
```

Set Google JSON securely as base64:

```bash
export GCP_SA_B64="$(base64 -i /Users/nijiachen/Downloads/double-scholar-487115-b1-f20d293ef8d3.json | tr -d '\n')"
heroku config:set GOOGLE_APPLICATION_CREDENTIALS_B64="$GCP_SA_B64" -a ai-pitching-coach
```

Deploy:

```bash
git push heroku main
```

Check:

```bash
heroku logs --tail -a ai-pitching-coach
curl https://ai-pitching-coach.herokuapp.com/health
```

## Quick Test

```bash
curl -F "audio=@/path/to/sample.webm" http://127.0.0.1:8000/api/jobs
curl http://127.0.0.1:8000/api/jobs/<job_id>
```

## Notes

- Upload limit is ~25MB.
- Local CORS defaults: `http://localhost:5173`, `http://127.0.0.1:5173`.
- In-memory jobs reset on restart; PostgreSQL jobs persist.
