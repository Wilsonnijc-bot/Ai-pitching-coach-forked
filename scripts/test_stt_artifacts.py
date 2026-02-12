#!/usr/bin/env python3
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.backend.gcs_utils import download_text, list_blobs  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Integration test for STT V2 artifact persistence.")
    parser.add_argument("--audio", required=True, help="Path to local audio file to upload.")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000", help="Backend base URL.")
    parser.add_argument("--bucket", default="audiosss1", help="GCS bucket used by backend.")
    parser.add_argument("--timeout-seconds", type=int, default=240, help="Polling timeout.")
    args = parser.parse_args()

    audio_path = Path(args.audio).expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    with httpx.Client(timeout=30.0, trust_env=False) as client:
        with audio_path.open("rb") as audio_file:
            files = {"audio": (audio_path.name, audio_file, "application/octet-stream")}
            create_resp = client.post(f"{args.api_base}/api/jobs", files=files)
            create_resp.raise_for_status()
            job_id = create_resp.json()["job_id"]
            print(f"created job: {job_id}")

        started = time.time()
        final_payload = None
        while time.time() - started < args.timeout_seconds:
            poll_resp = client.get(f"{args.api_base}/api/jobs/{job_id}")
            poll_resp.raise_for_status()
            payload = poll_resp.json()
            status = payload.get("status")
            if status in {"done", "failed"}:
                final_payload = payload
                break
            time.sleep(2)

    if not final_payload:
        raise TimeoutError(f"Timed out waiting for job completion: {job_id}")
    if final_payload.get("status") != "done":
        raise RuntimeError(f"Job failed: {final_payload.get('error')}")

    artifacts_prefix = f"jobs/{job_id}/artifacts/"
    blobs = list_blobs(artifacts_prefix, bucket=args.bucket)
    print("artifact blobs:")
    for blob in blobs:
        print(f"- gs://{args.bucket}/{blob}")

    required = {
        f"{artifacts_prefix}transcript.txt",
        f"{artifacts_prefix}words.json",
        f"{artifacts_prefix}diarization.json",
        f"{artifacts_prefix}meta.json",
    }
    missing = sorted(required - set(blobs))
    if missing:
        raise RuntimeError(f"Missing artifacts: {missing}")

    words = json.loads(download_text(args.bucket, f"{artifacts_prefix}words.json"))
    if not isinstance(words, list):
        raise RuntimeError("words.json must be an array.")
    for item in words:
        if "start" not in item or "end" not in item or "word" not in item:
            raise RuntimeError(f"Invalid words.json entry: {item}")

    diarization = json.loads(download_text(args.bucket, f"{artifacts_prefix}diarization.json"))
    if "word_speaker_tags_present" not in diarization:
        raise RuntimeError("diarization.json missing word_speaker_tags_present.")

    print(f"words count: {len(words)}")
    print(f"diarization available: {diarization.get('word_speaker_tags_present')}")
    print("artifact integration test passed")


if __name__ == "__main__":
    main()
