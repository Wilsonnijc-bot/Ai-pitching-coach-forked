#!/usr/bin/env python3
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.backend.gcs_utils import delete_prefix, download_text, upload_bytes


def main() -> None:
    bucket = os.getenv("GCS_AUDIO_BUCKET", "audiosss1").strip()
    if not bucket:
        raise RuntimeError("GCS_AUDIO_BUCKET is not set.")

    test_prefix = f"diag/{uuid.uuid4().hex}/"
    blob_path = f"{test_prefix}test.txt"
    payload = f"gcs-diag-{uuid.uuid4().hex}"

    uri = upload_bytes(bucket, blob_path, payload.encode("utf-8"), content_type="text/plain")
    print(f"Uploaded: {uri}")

    roundtrip = download_text(bucket, blob_path)
    print(f"Downloaded text: {roundtrip}")

    if roundtrip != payload:
        raise RuntimeError("GCS roundtrip mismatch.")

    delete_prefix(test_prefix, bucket=bucket)
    print(f"Deleted prefix: gs://{bucket}/{test_prefix}")
    print("GCS diagnostics passed.")


if __name__ == "__main__":
    main()
