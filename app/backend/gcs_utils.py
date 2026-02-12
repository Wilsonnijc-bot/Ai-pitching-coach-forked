import logging
import os
import json
from pathlib import Path
from typing import Optional, Tuple

from google.api_core.exceptions import NotFound
from google.cloud import storage


logger = logging.getLogger("uvicorn.error")
_storage_client: Optional[storage.Client] = None


def get_default_bucket() -> str:
    bucket = os.getenv("GCS_AUDIO_BUCKET", "audiosss1").strip()
    if not bucket:
        raise RuntimeError("GCS_AUDIO_BUCKET is not set.")
    return bucket


def get_storage_client() -> storage.Client:
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client()
    return _storage_client


def normalize_blob_path(blob_path: str) -> str:
    return blob_path.lstrip("/")


def build_gs_uri(bucket: str, blob_path: str) -> str:
    return f"gs://{bucket}/{normalize_blob_path(blob_path)}"


def parse_gcs_uri(gcs_uri: str) -> Tuple[str, str]:
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")
    remainder = gcs_uri[5:]
    if "/" not in remainder:
        raise ValueError(f"GCS URI is missing object path: {gcs_uri}")
    bucket, blob_path = remainder.split("/", 1)
    if not bucket or not blob_path:
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")
    return bucket, blob_path


def upload_bytes(bucket: str, blob_path: str, data: bytes, content_type: str) -> str:
    client = get_storage_client()
    clean_path = normalize_blob_path(blob_path)
    blob = client.bucket(bucket).blob(clean_path)
    blob.upload_from_string(data, content_type=content_type)
    return build_gs_uri(bucket, clean_path)


def upload_text(
    bucket: str,
    blob_path: str,
    text: str,
    content_type: str = "text/plain; charset=utf-8",
) -> str:
    data = (text or "").encode("utf-8")
    return upload_bytes(bucket, blob_path, data, content_type=content_type)


def upload_json(bucket: str, blob_path: str, obj) -> str:
    payload = json.dumps(obj, ensure_ascii=False)
    return upload_bytes(
        bucket,
        blob_path,
        payload.encode("utf-8"),
        content_type="application/json",
    )


def upload_file(bucket: str, blob_path: str, local_path: Path, content_type: str) -> str:
    if not local_path.exists():
        raise FileNotFoundError(f"Local file does not exist: {local_path}")
    client = get_storage_client()
    clean_path = normalize_blob_path(blob_path)
    blob = client.bucket(bucket).blob(clean_path)
    blob.upload_from_filename(str(local_path), content_type=content_type)
    return build_gs_uri(bucket, clean_path)


def download_text(bucket: str, blob_path: str) -> str:
    client = get_storage_client()
    clean_path = normalize_blob_path(blob_path)
    blob = client.bucket(bucket).blob(clean_path)
    if not blob.exists():
        raise FileNotFoundError(f"GCS object not found: gs://{bucket}/{clean_path}")
    return blob.download_as_text()


def list_blobs(prefix: str, bucket: Optional[str] = None) -> list[str]:
    bucket_name = bucket or get_default_bucket()
    client = get_storage_client()
    clean_prefix = normalize_blob_path(prefix)
    return sorted(
        blob.name
        for blob in client.list_blobs(bucket_name, prefix=clean_prefix)
        if blob.name and not blob.name.endswith("/")
    )


def delete_blob(bucket: str, blob_path: str) -> None:
    client = get_storage_client()
    clean_path = normalize_blob_path(blob_path)
    blob = client.bucket(bucket).blob(clean_path)
    try:
        blob.delete()
    except NotFound:
        return
    except Exception:
        logger.warning(
            "Failed deleting GCS object: gs://%s/%s",
            bucket,
            clean_path,
            exc_info=True,
        )


def delete_prefix(prefix: str, bucket: Optional[str] = None) -> None:
    bucket_name = bucket or get_default_bucket()
    client = get_storage_client()
    clean_prefix = normalize_blob_path(prefix)
    blobs = list(client.list_blobs(bucket_name, prefix=clean_prefix))
    for blob in blobs:
        try:
            blob.delete()
        except NotFound:
            continue
        except Exception:
            logger.warning(
                "Failed deleting GCS object during prefix cleanup: gs://%s/%s",
                bucket_name,
                blob.name,
                exc_info=True,
            )
