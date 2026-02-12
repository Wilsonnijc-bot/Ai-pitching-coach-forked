import json
import os
from typing import Any, Dict, List

import httpx


DEFAULT_BASE_URL = "https://api.gptsapi.net/v1"
DEFAULT_MODEL = "gpt-5.1-chat"
DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_PROVIDER_ERROR_CHARS = 1200

SYSTEM_PROMPT = (
    "You are an expert pitch analyst. Return ONLY valid JSON. No markdown. "
    "No code fences. No extra text. Use double quotes for all JSON strings."
)

USER_PROMPT_TEMPLATE = """Summarize the following pitch transcript in English. Output must follow this JSON schema exactly and be professional and elaborated (but still concise enough to fit in JSON):

{
  "title": string,
  "one_sentence_summary": string,
  "key_points": string[3-7],
  "audience": string,
  "ask_or_goal": string,
  "clarity_score": integer(1-10),
  "confidence": "low"|"medium"|"high",
  "red_flags": string[0-5],
  "next_steps": string[3-7]
}

Transcript:
<<<{TRANSCRIPT_TEXT}>>>
"""


def _truncate(text: str, max_chars: int = MAX_PROVIDER_ERROR_CHARS) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


def _get_api_key() -> str:
    api_key = os.getenv("GPTSAPI_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "Missing GPTSAPI_KEY. Set it before calling /api/jobs/{job_id}/summarize "
            '(example: export GPTSAPI_KEY="YOUR_KEY_HERE").'
        )
    return api_key


def _base_url() -> str:
    return os.getenv("GPTSAPI_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL


def _model_name() -> str:
    return os.getenv("GPTSAPI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _auth_headers() -> Dict[str, str]:
    api_key = _get_api_key()
    mode = os.getenv("GPTSAPI_AUTH_MODE", "authorization").strip().lower()
    headers = {"Content-Type": "application/json"}
    if mode == "x-api-key":
        headers["x-api-key"] = api_key
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _extract_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
        return "\n".join(parts).strip()
    return str(value or "").strip()


def _is_temperature_unsupported(error_message: str) -> bool:
    lowered = (error_message or "").lower()
    return "temperature" in lowered and "default (1)" in lowered


def _is_response_format_unsupported(error_message: str) -> bool:
    lowered = (error_message or "").lower()
    return "response_format" in lowered or "json_object" in lowered


def build_summary_user_prompt(transcript_text: str, deck_text: str | None = None) -> str:
    prompt = USER_PROMPT_TEMPLATE.replace("{TRANSCRIPT_TEXT}", transcript_text.strip())
    if deck_text and deck_text.strip():
        prompt += f"\nDeck context:\n<<<{deck_text.strip()}>>>"
    return prompt


def request_chat_completion(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 1800,
    response_format: Dict[str, Any] | None = None,
) -> str:
    payload = {
        "model": _model_name(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    timeout_seconds = float(os.getenv("GPTSAPI_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
    endpoint = _base_url().rstrip("/") + "/chat/completions"

    def _send(json_payload: Dict[str, Any]) -> httpx.Response:
        try:
            return httpx.post(
                endpoint,
                headers=_auth_headers(),
                json=json_payload,
                timeout=timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"GPTsAPI request timed out after {int(timeout_seconds)} seconds.") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Failed to call GPTsAPI: {exc}") from exc

    active_payload = dict(payload)
    response = _send(active_payload)
    for _ in range(2):
        if response.status_code < 400:
            break
        detail = ""
        try:
            error_payload = response.json()
            detail = (
                error_payload.get("error", {}).get("message")
                if isinstance(error_payload, dict)
                else ""
            ) or ""
        except Exception:
            detail = response.text or ""

        if response.status_code != 400:
            break

        retried = False
        if "response_format" in active_payload and _is_response_format_unsupported(detail):
            active_payload = dict(active_payload)
            active_payload.pop("response_format", None)
            retried = True
        elif "temperature" in active_payload and _is_temperature_unsupported(detail):
            active_payload = dict(active_payload)
            active_payload.pop("temperature", None)
            retried = True

        if not retried:
            break
        response = _send(active_payload)

    if response.status_code >= 400:
        detail = ""
        try:
            error_payload = response.json()
            detail = (
                error_payload.get("error", {}).get("message")
                if isinstance(error_payload, dict)
                else ""
            ) or ""
        except Exception:
            detail = response.text or ""
        detail = _truncate(detail or "Unknown provider error")
        raise RuntimeError(f"GPTsAPI error {response.status_code}: {detail}")

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError("GPTsAPI returned a non-JSON HTTP response.") from exc

    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not choices:
        raise RuntimeError("GPTsAPI response did not contain choices.")

    first_choice = choices[0] if isinstance(choices, list) else None
    message = first_choice.get("message") if isinstance(first_choice, dict) else None
    content = _extract_content(message.get("content") if isinstance(message, dict) else "")
    if not content:
        raise RuntimeError("GPTsAPI returned empty assistant content.")
    return content
