from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from .coaching_input import SharedCoachingInput, load_shared_input
from .prompts.round1 import ROUND_1_VERSION, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from .storage import JobStore


logger = logging.getLogger("uvicorn.error")
DEFAULT_BASE_URL = "https://api.gptsapi.net/v1"
DEFAULT_MODEL = "gpt-5.1-chat"
DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_ERROR_CHARS = 1200
EXPECTED_CRITERIA = {
    "Problem & Target User",
    "Value Proposition (10x & Switching)",
    "Differentiation & Defensibility",
}


def _truncate(text: str, max_chars: int = MAX_ERROR_CHARS) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


def _get_api_key() -> str:
    api_key = os.getenv("GPTSAPI_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "Missing GPTSAPI_KEY. Set it before calling /api/jobs/{job_id}/feedback/round1."
        )
    return api_key


def _build_client() -> OpenAI:
    base_url = os.getenv("GPTSAPI_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    timeout = float(os.getenv("GPTSAPI_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
    return OpenAI(base_url=base_url, api_key=_get_api_key(), timeout=timeout)


def _extract_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
        return "\n".join(parts).strip()
    return str(value or "").strip()


def _status_message(exc: APIStatusError) -> str:
    return (getattr(exc, "message", "") or str(exc)).lower()


def _unsupported_response_format(exc: APIStatusError) -> bool:
    message = _status_message(exc)
    return "response_format" in message or "json_object" in message


def _unsupported_temperature(exc: APIStatusError) -> bool:
    message = _status_message(exc)
    return "temperature" in message and "default (1)" in message


def _request_round1_content(shared_input: SharedCoachingInput) -> str:
    client = _build_client()
    model = os.getenv("GPTSAPI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    user_prompt = USER_PROMPT_TEMPLATE.replace(
        "{transcript_full_text}",
        shared_input.transcript_full_text,
    ).replace(
        "{deck_text_or_empty}",
        shared_input.deck_text or "",
    )

    base_kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 1800,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }

    attempts = [
        dict(base_kwargs),
        {k: v for k, v in base_kwargs.items() if k != "temperature"},
        {k: v for k, v in base_kwargs.items() if k != "response_format"},
        {k: v for k, v in base_kwargs.items() if k not in {"temperature", "response_format"}},
    ]
    seen_signatures: set[str] = set()
    last_status_error: APIStatusError | None = None

    for kwargs in attempts:
        signature = json.dumps(sorted(kwargs.keys()))
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)

        try:
            response = client.chat.completions.create(**kwargs)
            choice = response.choices[0] if response.choices else None
            if choice is None:
                raise RuntimeError("Round 1 response did not contain choices.")
            content = _extract_content(choice.message.content)
            if not content:
                raise RuntimeError("Round 1 response content is empty.")
            return content
        except APIStatusError as exc:
            last_status_error = exc
            if _unsupported_response_format(exc) or _unsupported_temperature(exc):
                continue
            status_code = getattr(exc, "status_code", None)
            detail = getattr(exc, "message", None) or str(exc)
            if status_code is not None:
                raise RuntimeError(f"Round 1 LLM request failed ({status_code}): {detail}") from exc
            raise RuntimeError(f"Round 1 LLM request failed: {detail}") from exc
        except APITimeoutError as exc:
            raise RuntimeError("Round 1 LLM request timed out.") from exc
        except APIConnectionError as exc:
            raise RuntimeError(f"Failed to connect to LLM provider: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Unexpected Round 1 LLM error: {exc}") from exc

    if last_status_error is not None:
        status_code = getattr(last_status_error, "status_code", None)
        detail = getattr(last_status_error, "message", None) or str(last_status_error)
        if status_code is not None:
            raise RuntimeError(f"Round 1 LLM request failed ({status_code}): {detail}")
        raise RuntimeError(f"Round 1 LLM request failed: {detail}")
    raise RuntimeError("Round 1 LLM request failed before receiving a response.")


def _parse_json_with_repair(raw_content: str) -> dict:
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError:
        start = raw_content.find("{")
        end = raw_content.rfind("}")
        if start == -1 or end <= start:
            raise RuntimeError("Round 1 output is not valid JSON.")
        try:
            parsed = json.loads(raw_content[start : end + 1])
        except json.JSONDecodeError as exc:
            raise RuntimeError("Round 1 output could not be repaired into valid JSON.") from exc

    if not isinstance(parsed, dict):
        raise RuntimeError("Round 1 JSON root must be an object.")
    return parsed


def _validate_round1_schema(payload: dict) -> dict:
    if payload.get("round") != 1:
        raise RuntimeError('Round 1 payload must contain "round": 1.')
    if not isinstance(payload.get("title"), str) or not str(payload.get("title")).strip():
        raise RuntimeError('Round 1 payload must contain a non-empty "title" string.')

    sections = payload.get("sections")
    if not isinstance(sections, list) or len(sections) != 3:
        raise RuntimeError('Round 1 payload must contain exactly 3 "sections".')

    top_actions = payload.get("top_3_actions_for_next_pitch")
    if not isinstance(top_actions, list):
        raise RuntimeError('Round 1 payload must include "top_3_actions_for_next_pitch" as an array.')

    seen_criteria: set[str] = set()
    for section in sections:
        if not isinstance(section, dict):
            raise RuntimeError("Each section must be an object.")

        for key in (
            "criterion",
            "verdict",
            "evidence_quotes",
            "what_investors_will_question",
            "missing_information",
            "recommended_rewrites",
        ):
            if key not in section:
                raise RuntimeError(f'Round 1 section is missing required key "{key}".')

        criterion = str(section.get("criterion") or "").strip()
        verdict = str(section.get("verdict") or "").strip().lower()
        if criterion not in EXPECTED_CRITERIA:
            raise RuntimeError(f'Unexpected round 1 criterion "{criterion}".')
        if verdict not in {"strong", "mixed", "weak"}:
            raise RuntimeError(f'Invalid verdict "{verdict}" in section "{criterion}".')
        seen_criteria.add(criterion)

        for list_field in (
            "evidence_quotes",
            "what_investors_will_question",
            "missing_information",
            "recommended_rewrites",
        ):
            if not isinstance(section.get(list_field), list):
                raise RuntimeError(f'"{list_field}" must be an array in section "{criterion}".')

    if seen_criteria != EXPECTED_CRITERIA:
        raise RuntimeError("Round 1 sections do not match required criteria.")

    return payload


def run_round1(job_store: JobStore, job_id: str) -> dict:
    job_store.update_job(
        job_id,
        feedback_round_1_status="running",
        feedback_round_1_error=None,
        feedback_round_1_version=ROUND_1_VERSION,
    )

    try:
        shared_input = load_shared_input(job_store, job_id)
        raw_content = _request_round1_content(shared_input)
        parsed = _validate_round1_schema(_parse_json_with_repair(raw_content))

        job_store.update_job(
            job_id,
            feedback_round_1=parsed,
            feedback_round_1_version=ROUND_1_VERSION,
            feedback_round_1_status="done",
            feedback_round_1_error=None,
            error=None,
        )
        logger.info("job_id=%s round1_feedback_done", job_id)
        return parsed
    except Exception as exc:
        message = _truncate(str(exc))
        job_store.update_job(
            job_id,
            feedback_round_1_status="failed",
            feedback_round_1_error=message,
        )
        logger.warning("job_id=%s round1_feedback_failed error=%s", job_id, message)
        raise
