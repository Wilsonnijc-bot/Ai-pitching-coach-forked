import os
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI


DEFAULT_BASE_URL = "https://api.gptsapi.net/v1"
DEFAULT_MODEL = "gpt-5.1-chat"


def _get_api_key() -> str:
    api_key = os.getenv("GPTSAPI_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "Missing GPTSAPI_KEY. Set it before calling /api/jobs/{job_id}/llm_test "
            '(example: export GPTSAPI_KEY="YOUR_KEY_HERE").'
        )
    return api_key


def _build_client() -> OpenAI:
    base_url = os.getenv("GPTSAPI_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    timeout_seconds = float(os.getenv("GPTSAPI_TIMEOUT_SECONDS", "60"))
    return OpenAI(
        base_url=base_url,
        api_key=_get_api_key(),
        timeout=timeout_seconds,
    )


def _extract_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
        return "\n".join(parts).strip()
    return str(value or "").strip()


def _is_temperature_unsupported(exc: APIStatusError) -> bool:
    message = (getattr(exc, "message", "") or str(exc)).lower()
    return "temperature" in message and "default (1)" in message


def run_llm_test_prompt(transcript_text: str) -> str:
    transcript = (transcript_text or "").strip()
    if not transcript:
        raise ValueError("Transcript text is empty.")

    model = os.getenv("GPTSAPI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    client = _build_client()

    request_kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {
                "role": "user",
                "content": (
                    "Here is a pitch transcript. Summarize it in 5 bullet points, in English:\n\n"
                    f"{transcript}"
                ),
            },
        ],
        "max_tokens": 800,
    }

    try:
        response = client.chat.completions.create(
            **request_kwargs,
            temperature=0.3,
        )
    except APIStatusError as exc:
        if _is_temperature_unsupported(exc):
            response = client.chat.completions.create(**request_kwargs)
        else:
            provider_message = getattr(exc, "message", str(exc))
            status_code = getattr(exc, "status_code", None)
            if status_code is not None:
                raise RuntimeError(f"GPTsAPI request failed ({status_code}): {provider_message}") from exc
            raise RuntimeError(f"GPTsAPI request failed: {provider_message}") from exc
    except APITimeoutError as exc:
        raise RuntimeError("LLM request timed out while calling GPTsAPI.") from exc
    except APIConnectionError as exc:
        raise RuntimeError(f"Failed to connect to GPTsAPI: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Unexpected LLM error: {exc}") from exc

    try:
        choice = response.choices[0] if response.choices else None
    except Exception as exc:
        raise RuntimeError(f"Unexpected LLM response shape: {exc}") from exc

    if choice is None:
        raise RuntimeError("GPTsAPI returned no choices.")

    content = _extract_content(choice.message.content)
    if not content:
        raise RuntimeError("GPTsAPI returned an empty response.")
    return content
