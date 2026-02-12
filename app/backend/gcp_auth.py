import base64
import json
import os
from functools import lru_cache
from typing import Optional

from google.oauth2 import service_account


CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def _decode_base64_json(encoded: str) -> dict:
    normalized = encoded.strip()
    padding_needed = (-len(normalized)) % 4
    if padding_needed:
        normalized += "=" * padding_needed

    try:
        decoded = base64.b64decode(normalized)
    except Exception as exc:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS_B64 is not valid base64."
        ) from exc

    return _parse_service_account_json(decoded.decode("utf-8"), "GOOGLE_APPLICATION_CREDENTIALS_B64")


def _parse_service_account_json(raw_json: str, source: str) -> dict:
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{source} does not contain valid JSON.") from exc

    if not isinstance(parsed, dict):
        raise RuntimeError(f"{source} must contain a JSON object.")
    return parsed


@lru_cache(maxsize=1)
def get_gcp_credentials() -> Optional[service_account.Credentials]:
    env_b64 = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_B64", "").strip()
    if env_b64:
        info = _decode_base64_json(env_b64)
        return service_account.Credentials.from_service_account_info(
            info,
            scopes=[CLOUD_PLATFORM_SCOPE],
        )

    env_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
    if env_json:
        info = _parse_service_account_json(env_json, "GOOGLE_APPLICATION_CREDENTIALS_JSON")
        return service_account.Credentials.from_service_account_info(
            info,
            scopes=[CLOUD_PLATFORM_SCOPE],
        )

    env_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if env_path:
        if not os.path.exists(env_path):
            raise RuntimeError(
                f"GOOGLE_APPLICATION_CREDENTIALS points to a missing file: {env_path}"
            )
        return service_account.Credentials.from_service_account_file(
            env_path,
            scopes=[CLOUD_PLATFORM_SCOPE],
        )

    return None


def get_project_id_hint() -> Optional[str]:
    explicit = os.getenv("GCP_PROJECT_ID", "").strip()
    if explicit:
        return explicit

    creds = get_gcp_credentials()
    if creds is not None:
        return getattr(creds, "project_id", None)
    return None
