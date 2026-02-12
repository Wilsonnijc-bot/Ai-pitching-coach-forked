import base64
import json
import os
from pathlib import Path
from typing import List

from google.cloud import speech
from google.oauth2 import service_account

from .models import duration_to_seconds


def load_service_account_credentials():
    credentials_b64 = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_B64", "").strip()
    credentials_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

    if credentials_b64:
        try:
            payload = base64.b64decode(credentials_b64).decode("utf-8")
            info = json.loads(payload)
        except Exception as exc:
            raise RuntimeError(f"Invalid GOOGLE_APPLICATION_CREDENTIALS_B64: {exc}") from exc
        return service_account.Credentials.from_service_account_info(info)

    if credentials_json:
        try:
            info = json.loads(credentials_json)
        except Exception as exc:
            raise RuntimeError(f"Invalid GOOGLE_APPLICATION_CREDENTIALS_JSON: {exc}") from exc
        return service_account.Credentials.from_service_account_info(info)

    if credentials_path:
        if not Path(credentials_path).exists():
            raise RuntimeError(
                f"GOOGLE_APPLICATION_CREDENTIALS points to a missing file: {credentials_path}"
            )
        return None

    raise RuntimeError(
        "Google credentials are not configured. Set one of "
        "GOOGLE_APPLICATION_CREDENTIALS, GOOGLE_APPLICATION_CREDENTIALS_JSON, "
        "or GOOGLE_APPLICATION_CREDENTIALS_B64."
    )


def build_speech_client() -> speech.SpeechClient:
    credentials = load_service_account_credentials()
    if credentials is None:
        return speech.SpeechClient()
    return speech.SpeechClient(credentials=credentials)


def parse_speech_response(response) -> dict:
    full_text_parts: List[str] = []
    segments: List[dict] = []
    words: List[dict] = []

    for result in response.results:
        if not result.alternatives:
            continue
        alternative = result.alternatives[0]
        transcript = (alternative.transcript or "").strip()
        if transcript:
            full_text_parts.append(transcript)

        result_words = list(alternative.words or [])
        if result_words:
            segment_start = duration_to_seconds(result_words[0].start_time)
            segment_end = duration_to_seconds(result_words[-1].end_time)
        else:
            segment_start = 0.0
            segment_end = 0.0

        segments.append(
            {
                "start": segment_start,
                "end": segment_end,
                "text": transcript,
            }
        )

        for word_info in result_words:
            words.append(
                {
                    "start": duration_to_seconds(word_info.start_time),
                    "end": duration_to_seconds(word_info.end_time),
                    "word": word_info.word,
                }
            )

    return {
        "full_text": " ".join(full_text_parts).strip(),
        "segments": segments,
        "words": words,
    }
