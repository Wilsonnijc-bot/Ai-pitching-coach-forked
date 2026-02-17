from __future__ import annotations

import json
import logging

from .coaching_input import SharedCoachingInput
from .coaching_input import load_shared_input
from .llm_gptsapi import request_chat_completion
from .prompts.round2 import ROUND_2_VERSION, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from .storage import JobStore


logger = logging.getLogger("uvicorn.error")
MAX_ERROR_CHARS = 1200
EXPECTED_CRITERIA = {
    "Clarity & Conviction",
    "Business Model",
    "Market Potential",
}


def _truncate(text: str, max_chars: int = MAX_ERROR_CHARS) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


def _parse_json(raw_content: str) -> dict:
    parsed = json.loads(raw_content)
    if not isinstance(parsed, dict):
        raise RuntimeError("Round 2 JSON root must be an object.")
    return parsed


def _validate_round2_schema(payload: dict) -> dict:
    if payload.get("round") != 2:
        raise RuntimeError('Round 2 payload must contain "round": 2.')
    if not isinstance(payload.get("title"), str) or not str(payload.get("title")).strip():
        raise RuntimeError('Round 2 payload must contain a non-empty "title" string.')

    sections = payload.get("sections")
    if not isinstance(sections, list) or len(sections) != 3:
        raise RuntimeError('Round 2 payload must contain exactly 3 "sections".')

    top_actions = payload.get("top_3_actions_for_next_pitch")
    if not isinstance(top_actions, list):
        raise RuntimeError('Round 2 payload must include "top_3_actions_for_next_pitch" as an array.')

    required_by_criterion = {
        "Clarity & Conviction": {
            "diagnosis",
            "timing_signals_used",
            "what_investors_felt",
            "what_to_fix_next",
            "rewrite_lines_to_increase_conviction",
        },
        "Business Model": {
            "diagnosis",
            "missing_or_vague",
            "what_investors_need_to_hear",
            "recommended_lines",
        },
        "Market Potential": {
            "diagnosis",
            "missing_or_vague",
            "credible_market_framing",
            "recommended_lines",
        },
    }

    seen_criteria: set[str] = set()
    for section in sections:
        if not isinstance(section, dict):
            raise RuntimeError("Each round 2 section must be an object.")

        criterion = str(section.get("criterion") or "").strip()
        verdict = str(section.get("verdict") or "").strip().lower()
        if criterion not in EXPECTED_CRITERIA:
            raise RuntimeError(f'Unexpected round 2 criterion "{criterion}".')
        if verdict not in {"strong", "mixed", "weak"}:
            raise RuntimeError(f'Invalid verdict "{verdict}" in section "{criterion}".')
        seen_criteria.add(criterion)

        required_keys = required_by_criterion[criterion]
        for key in required_keys:
            if key not in section:
                raise RuntimeError(f'Round 2 section "{criterion}" is missing key "{key}".')
            if key != "timing_signals_used" and not isinstance(section.get(key), list):
                raise RuntimeError(f'Round 2 section "{criterion}" key "{key}" must be an array.')

        if criterion == "Clarity & Conviction":
            timing = section.get("timing_signals_used")
            if not isinstance(timing, dict):
                raise RuntimeError('"timing_signals_used" must be an object for Clarity & Conviction.')
            for metric_key in (
                "duration_seconds",
                "wpm",
                "pause_count",
                "longest_pause_seconds",
                "filler_count",
                "filler_rate_per_min",
                "top_fillers",
            ):
                if metric_key not in timing:
                    raise RuntimeError(f'"timing_signals_used" missing "{metric_key}".')
            if not isinstance(timing.get("top_fillers"), list):
                raise RuntimeError('"timing_signals_used.top_fillers" must be an array.')

    if seen_criteria != EXPECTED_CRITERIA:
        raise RuntimeError("Round 2 sections do not match required criteria.")

    return payload


def _build_round2_user_prompt(shared_input: SharedCoachingInput) -> str:
    derived_metrics_dict = shared_input.derived_metrics.model_dump()
    return (
        USER_PROMPT_TEMPLATE.replace(
            "{transcript_full_text}",
            shared_input.transcript_full_text,
        )
        .replace(
            "{derived_metrics_json}",
            json.dumps(derived_metrics_dict, ensure_ascii=False),
        )
        .replace(
            "{deck_text_or_empty}",
            shared_input.deck_text or "",
        )
    )


def _request_round2_output(user_prompt: str) -> str:
    return request_chat_completion(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.3,
        max_tokens=2200,
        response_format={"type": "json_object"},
    )


def _repair_prompt(invalid_output: str) -> str:
    return (
        "Your previous output was invalid JSON. Return ONLY corrected valid JSON matching "
        "the schema. Here is the invalid output:\n"
        f"<<<{invalid_output}>>>"
    )


def run_round2(job_store: JobStore, job_id: str) -> dict:
    job_store.update_job(
        job_id,
        feedback_round_2_status="running",
        feedback_round_2_error=None,
        feedback_round_2_version=ROUND_2_VERSION,
    )

    try:
        shared_input = load_shared_input(job_store, job_id)
        user_prompt = _build_round2_user_prompt(shared_input)
        raw_output = _request_round2_output(user_prompt)

        try:
            parsed = _validate_round2_schema(_parse_json(raw_output))
        except Exception:
            repaired_output = _request_round2_output(_repair_prompt(raw_output))
            parsed = _validate_round2_schema(_parse_json(repaired_output))

        job_store.update_job(
            job_id,
            feedback_round_2=parsed,
            feedback_round_2_version=ROUND_2_VERSION,
            feedback_round_2_status="done",
            feedback_round_2_error=None,
        )
        logger.info("job_id=%s round2_feedback_done", job_id)
        return parsed
    except Exception as exc:
        message = _truncate(str(exc))
        job_store.update_job(
            job_id,
            feedback_round_2_status="failed",
            feedback_round_2_error=message,
        )
        logger.warning("job_id=%s round2_feedback_failed error=%s", job_id, message)
        raise
