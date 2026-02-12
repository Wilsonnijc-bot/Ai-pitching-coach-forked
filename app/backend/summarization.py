import json
import logging
from typing import Any, Dict

from .llm_gptsapi import SYSTEM_PROMPT, build_summary_user_prompt, request_chat_completion
from .storage import JobStore


logger = logging.getLogger("uvicorn.error")
MAX_SUMMARY_ERROR_CHARS = 1200


def _truncate(text: str, max_chars: int = MAX_SUMMARY_ERROR_CHARS) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


def _validate_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string.")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field} must be a non-empty string.")
    return cleaned


def _validate_string_list(value: Any, field: str, minimum: int, maximum: int) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array of strings.")
    if not (minimum <= len(value) <= maximum):
        raise ValueError(f"{field} must contain {minimum}-{maximum} items.")
    cleaned: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field}[{index}] must be a non-empty string.")
        cleaned.append(item.strip())
    return cleaned


def validate_summary_schema(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Summary JSON root must be an object.")

    expected_keys = {
        "title",
        "one_sentence_summary",
        "key_points",
        "audience",
        "ask_or_goal",
        "clarity_score",
        "confidence",
        "red_flags",
        "next_steps",
    }
    provided_keys = set(payload.keys())
    if provided_keys != expected_keys:
        missing = sorted(expected_keys - provided_keys)
        extra = sorted(provided_keys - expected_keys)
        details = []
        if missing:
            details.append(f"missing keys: {', '.join(missing)}")
        if extra:
            details.append(f"extra keys: {', '.join(extra)}")
        raise ValueError("Summary JSON keys mismatch (" + "; ".join(details) + ").")

    clarity_score = payload.get("clarity_score")
    if not isinstance(clarity_score, int) or not (1 <= clarity_score <= 10):
        raise ValueError("clarity_score must be an integer between 1 and 10.")

    confidence = payload.get("confidence")
    if confidence not in {"low", "medium", "high"}:
        raise ValueError('confidence must be one of: "low", "medium", "high".')

    validated = {
        "title": _validate_string(payload.get("title"), "title"),
        "one_sentence_summary": _validate_string(
            payload.get("one_sentence_summary"), "one_sentence_summary"
        ),
        "key_points": _validate_string_list(payload.get("key_points"), "key_points", 3, 7),
        "audience": _validate_string(payload.get("audience"), "audience"),
        "ask_or_goal": _validate_string(payload.get("ask_or_goal"), "ask_or_goal"),
        "clarity_score": clarity_score,
        "confidence": confidence,
        "red_flags": _validate_string_list(payload.get("red_flags"), "red_flags", 0, 5),
        "next_steps": _validate_string_list(payload.get("next_steps"), "next_steps", 3, 7),
    }
    return validated


def _parse_summary_json(raw_content: str) -> Dict[str, Any]:
    parsed = json.loads(raw_content)
    return validate_summary_schema(parsed)


def _build_repair_prompt(invalid_output: str) -> str:
    return (
        "Your previous output was invalid JSON. Return ONLY corrected valid JSON "
        "that matches the schema. Here is the invalid output:\n"
        f"<<<{invalid_output}>>>"
    )


def _extract_transcript_text(job) -> str:
    transcript_payload = job.result if isinstance(job.result, dict) else {}
    transcript_text = str(transcript_payload.get("full_text") or "").strip()
    return transcript_text


def process_summary_job(job_store: JobStore, job_id: str) -> None:
    try:
        job_store.update_job(
            job_id,
            status="summarizing",
            progress=70,
            summary_json=None,
            summary_error=None,
            error=None,
        )

        job = job_store.get_job(job_id)
        if not job:
            raise RuntimeError("Job not found while summarizing.")

        transcript_text = _extract_transcript_text(job)
        if not transcript_text:
            raise RuntimeError("Transcript is missing for this job.")

        deck_text = job_store.get_deck_text(job_id)
        user_prompt = build_summary_user_prompt(transcript_text=transcript_text, deck_text=deck_text)

        raw_output = request_chat_completion(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.3,
            max_tokens=1800,
        )

        try:
            summary_json = _parse_summary_json(raw_output)
        except Exception:
            repaired_output = request_chat_completion(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=_build_repair_prompt(raw_output),
                temperature=0.3,
                max_tokens=1800,
            )
            summary_json = _parse_summary_json(repaired_output)

        job_store.update_job(job_id, progress=90)
        job_store.update_job(
            job_id,
            status="done",
            progress=100,
            summary_json=summary_json,
            summary_error=None,
            error=None,
        )
        logger.info(
            "job_id=%s summary_done title=%s key_points=%s",
            job_id,
            summary_json.get("title", ""),
            len(summary_json.get("key_points", [])),
        )
    except Exception as exc:
        message = _truncate(str(exc))
        job_store.update_job(
            job_id,
            status="failed",
            progress=100,
            summary_error=message,
            error=message,
        )
        logger.warning("job_id=%s summary_failed error=%s", job_id, message)
