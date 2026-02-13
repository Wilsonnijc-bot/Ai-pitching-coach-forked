from __future__ import annotations

import json
import logging

from .coaching_input import load_shared_input, SharedCoachingInput
from .llm_gptsapi import request_chat_completion
from .prompts.round3 import ROUND_3_VERSION, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from .storage import JobStore


logger = logging.getLogger("uvicorn.error")
MAX_ERROR_CHARS = 1200
EXPECTED_CRITERIA = {
    "Energy & Presence",
    "Pacing & Emphasis",
    "Tone-Product Alignment",
}


def _truncate(text: str, max_chars: int = MAX_ERROR_CHARS) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


def _parse_json(raw_content: str) -> dict:
    parsed = json.loads(raw_content)
    if not isinstance(parsed, dict):
        raise RuntimeError("Round 3 JSON root must be an object.")
    return parsed


def _validate_round3_schema(payload: dict) -> dict:
    if payload.get("round") != 3:
        raise RuntimeError('Round 3 payload must contain "round": 3.')
    if not isinstance(payload.get("title"), str) or not str(payload.get("title")).strip():
        raise RuntimeError('Round 3 payload must contain a non-empty "title" string.')

    sections = payload.get("sections")
    if not isinstance(sections, list) or len(sections) != 3:
        raise RuntimeError('Round 3 payload must contain exactly 3 "sections".')

    vocal_actions = payload.get("top_3_vocal_actions")
    if not isinstance(vocal_actions, list):
        raise RuntimeError('Round 3 payload must include "top_3_vocal_actions" as an array.')

    seen_criteria: set[str] = set()
    for section in sections:
        if not isinstance(section, dict):
            raise RuntimeError("Each round 3 section must be an object.")

        criterion = str(section.get("criterion") or "").strip()
        verdict = str(section.get("verdict") or "").strip().lower()
        if criterion not in EXPECTED_CRITERIA:
            raise RuntimeError(f'Unexpected round 3 criterion "{criterion}".')
        if verdict not in {"strong", "mixed", "weak"}:
            raise RuntimeError(f'Invalid verdict "{verdict}" in section "{criterion}".')
        seen_criteria.add(criterion)

        # Section-specific validation
        if criterion == "Energy & Presence":
            if not isinstance(section.get("energy_timeline_summary"), dict):
                raise RuntimeError('"energy_timeline_summary" must be an object.')
            if not isinstance(section.get("well_delivered_moments"), list):
                raise RuntimeError('"well_delivered_moments" must be an array.')
            if not isinstance(section.get("misaligned_moments"), list):
                raise RuntimeError('"misaligned_moments" must be an array.')

        elif criterion == "Pacing & Emphasis":
            if not isinstance(section.get("overall_assessment"), list):
                raise RuntimeError('"overall_assessment" must be an array.')
            if not isinstance(section.get("rushed_important_sentences"), list):
                raise RuntimeError('"rushed_important_sentences" must be an array.')

        elif criterion == "Tone-Product Alignment":
            if not isinstance(section.get("inferred_product_type"), str):
                raise RuntimeError('"inferred_product_type" must be a string.')
            if not isinstance(section.get("why_this_tone"), str):
                raise RuntimeError('"why_this_tone" must be a string.')
            if not isinstance(section.get("your_actual_tone"), str):
                raise RuntimeError('"your_actual_tone" must be a string.')
            if not isinstance(section.get("alignment_assessment"), list):
                raise RuntimeError('"alignment_assessment" must be an array.')
            if not isinstance(section.get("target_tone_profile"), list):
                raise RuntimeError('"target_tone_profile" must be an array.')
            if not isinstance(section.get("recommended_adjustments"), list):
                raise RuntimeError('"recommended_adjustments" must be an array.')

    if seen_criteria != EXPECTED_CRITERIA:
        raise RuntimeError("Round 3 sections do not match required criteria.")

    return payload


def _build_round3_user_prompt(shared_input: SharedCoachingInput) -> str:
    derived = shared_input.derived_metrics.model_dump()

    energy_timeline = derived.get("energy_timeline") or []
    sentence_pacing = derived.get("sentence_pacing") or []

    return (
        USER_PROMPT_TEMPLATE
        .replace("{energy_timeline_json}", json.dumps(energy_timeline, ensure_ascii=False))
        .replace("{sentence_pacing_json}", json.dumps(sentence_pacing, ensure_ascii=False))
        .replace("{transcript_full_text}", shared_input.transcript_full_text)
        .replace("{deck_text_or_empty}", shared_input.deck_text or "")
    )


def _request_round3_output(user_prompt: str) -> str:
    return request_chat_completion(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.3,
        max_tokens=2500,
        response_format={"type": "json_object"},
    )


def _repair_prompt(invalid_output: str) -> str:
    return (
        "Your previous output was invalid JSON. Return ONLY corrected valid JSON matching "
        "the schema. Here is the invalid output:\n"
        f"<<<{invalid_output}>>>"
    )


def run_round3(job_store: JobStore, job_id: str) -> dict:
    job_store.update_job(
        job_id,
        feedback_round_3_status="running",
        feedback_round_3_error=None,
        feedback_round_3_version=ROUND_3_VERSION,
    )

    try:
        shared_input = load_shared_input(job_store, job_id)

        # Check that tone data is available
        derived = shared_input.derived_metrics.model_dump()
        if not derived.get("energy_timeline") and not derived.get("sentence_pacing"):
            raise RuntimeError(
                "Tone metrics (energy_timeline / sentence_pacing) are not available for this job. "
                "The audio may have been too short or processing failed."
            )

        user_prompt = _build_round3_user_prompt(shared_input)
        raw_output = _request_round3_output(user_prompt)

        try:
            parsed = _validate_round3_schema(_parse_json(raw_output))
        except Exception:
            repaired_output = _request_round3_output(_repair_prompt(raw_output))
            parsed = _validate_round3_schema(_parse_json(repaired_output))

        job_store.update_job(
            job_id,
            feedback_round_3=parsed,
            feedback_round_3_version=ROUND_3_VERSION,
            feedback_round_3_status="done",
            feedback_round_3_error=None,
        )
        logger.info("job_id=%s round3_feedback_done", job_id)
        return parsed
    except Exception as exc:
        message = _truncate(str(exc))
        job_store.update_job(
            job_id,
            feedback_round_3_status="failed",
            feedback_round_3_error=message,
        )
        logger.warning("job_id=%s round3_feedback_failed error=%s", job_id, message)
        raise
