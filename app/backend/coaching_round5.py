from __future__ import annotations

import json
import logging

from .coaching_input import SharedCoachingInput, load_shared_input
from .llm_gptsapi import request_chat_completion
from .prompts.round5 import ROUND_5_VERSION, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from .storage import JobStore


logger = logging.getLogger("uvicorn.error")
MAX_ERROR_CHARS = 1200
EXPECTED_CRITERIA = {"Overview", "Pitch Deck Evaluation"}


def _truncate(text: str, max_chars: int = MAX_ERROR_CHARS) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


def _parse_json(raw_content: str) -> dict:
    parsed = json.loads(raw_content)
    if not isinstance(parsed, dict):
        raise RuntimeError("Round 5 JSON root must be an object.")
    return parsed


def _validate_round5_schema(payload: dict) -> dict:
    if payload.get("round") != 5:
        raise RuntimeError('Round 5 payload must contain "round": 5.')
    if not isinstance(payload.get("title"), str) or not str(payload.get("title")).strip():
        raise RuntimeError('Round 5 payload must contain a non-empty "title" string.')

    sections = payload.get("sections")
    if not isinstance(sections, list) or len(sections) != 2:
        raise RuntimeError('Round 5 payload must contain exactly 2 "sections".')

    seen_criteria: set[str] = set()
    for section in sections:
        if not isinstance(section, dict):
            raise RuntimeError("Each round 5 section must be an object.")

        criterion = str(section.get("criterion") or "").strip()
        verdict = str(section.get("verdict") or "").strip().lower()
        if criterion not in EXPECTED_CRITERIA:
            raise RuntimeError(f'Unexpected round 5 criterion "{criterion}".')
        if verdict not in {"strong", "mixed", "weak"}:
            raise RuntimeError(f'Invalid verdict "{verdict}" in section "{criterion}".')
        seen_criteria.add(criterion)

        if criterion == "Overview":
            for key in (
                "overall_evaluation",
                "summary_of_content_analysis",
                "summary_of_delivery_analysis",
            ):
                if not isinstance(section.get(key), str):
                    raise RuntimeError(f'"{key}" must be a string in Overview.')
            if not isinstance(section.get("key_strengths"), list):
                raise RuntimeError('"key_strengths" must be an array in Overview.')
            if not isinstance(section.get("areas_of_improvement"), list):
                raise RuntimeError('"areas_of_improvement" must be an array in Overview.')

        if criterion == "Pitch Deck Evaluation":
            if not isinstance(section.get("overall_assessment"), str):
                raise RuntimeError('"overall_assessment" must be a string in Pitch Deck Evaluation.')
            lacking_content = section.get("lacking_content")
            if not isinstance(lacking_content, list):
                raise RuntimeError('"lacking_content" must be an array in Pitch Deck Evaluation.')
            for item in lacking_content:
                if not isinstance(item, dict):
                    raise RuntimeError("Each lacking_content item must be an object.")
                if not isinstance(item.get("what"), str) or not isinstance(item.get("why"), str):
                    raise RuntimeError('Each lacking_content item must include string "what" and "why".')

            structural_flow_issues = section.get("structural_flow_issues")
            if not isinstance(structural_flow_issues, list):
                raise RuntimeError('"structural_flow_issues" must be an array in Pitch Deck Evaluation.')
            for item in structural_flow_issues:
                if not isinstance(item, dict):
                    raise RuntimeError("Each structural_flow_issues item must be an object.")
                if not isinstance(item.get("issue"), str) or not isinstance(item.get("impact"), str):
                    raise RuntimeError('Each structural_flow_issues item must include string "issue" and "impact".')

            if not isinstance(section.get("recommended_refinements"), list):
                raise RuntimeError('"recommended_refinements" must be an array in Pitch Deck Evaluation.')

    if seen_criteria != EXPECTED_CRITERIA:
        raise RuntimeError("Round 5 sections do not match required criteria.")

    return payload


def _build_round5_user_prompt(
    shared_input: SharedCoachingInput,
    *,
    round1_feedback: dict,
    round2_feedback: dict,
    round3_feedback: dict,
    round4_feedback: dict,
) -> str:
    return (
        USER_PROMPT_TEMPLATE.replace(
            "{round1_feedback_json}",
            json.dumps(round1_feedback, ensure_ascii=False),
        )
        .replace(
            "{round2_feedback_json}",
            json.dumps(round2_feedback, ensure_ascii=False),
        )
        .replace(
            "{round3_feedback_json}",
            json.dumps(round3_feedback, ensure_ascii=False),
        )
        .replace(
            "{round4_feedback_json}",
            json.dumps(round4_feedback, ensure_ascii=False),
        )
        .replace("{transcript_full_text}", shared_input.transcript_full_text)
        .replace("{deck_text_or_empty}", shared_input.deck_text or "")
    )


def _request_round5_output(user_prompt: str) -> str:
    return request_chat_completion(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.3,
        max_tokens=3000,
        response_format={"type": "json_object"},
    )


def _repair_prompt(invalid_output: str) -> str:
    return (
        "Your previous output was invalid JSON. Return ONLY corrected valid JSON matching "
        "the schema. Here is the invalid output:\n"
        f"<<<{invalid_output}>>>"
    )


def run_round5(job_store: JobStore, job_id: str) -> dict:
    job_store.update_job(
        job_id,
        feedback_round_5_status="running",
        feedback_round_5_error=None,
        feedback_round_5_version=ROUND_5_VERSION,
    )

    try:
        job = job_store.get_job(job_id)
        if not job:
            raise RuntimeError(f"Job not found: {job_id}")

        prerequisites: list[str] = []
        for round_number in (1, 2, 3, 4):
            status = getattr(job, f"feedback_round_{round_number}_status", None)
            payload = getattr(job, f"feedback_round_{round_number}", None)
            if status != "done" or not isinstance(payload, dict):
                prerequisites.append(f"round{round_number}")
        if prerequisites:
            joined = ", ".join(prerequisites)
            raise RuntimeError(f"Round 5 requires completed rounds 1-4. Missing: {joined}.")

        shared_input = load_shared_input(job_store, job_id)
        user_prompt = _build_round5_user_prompt(
            shared_input,
            round1_feedback=job.feedback_round_1,
            round2_feedback=job.feedback_round_2,
            round3_feedback=job.feedback_round_3,
            round4_feedback=job.feedback_round_4,
        )
        raw_output = _request_round5_output(user_prompt)

        try:
            parsed = _validate_round5_schema(_parse_json(raw_output))
        except Exception:
            repaired_output = _request_round5_output(_repair_prompt(raw_output))
            parsed = _validate_round5_schema(_parse_json(repaired_output))

        job_store.update_job(
            job_id,
            feedback_round_5=parsed,
            feedback_round_5_version=ROUND_5_VERSION,
            feedback_round_5_status="done",
            feedback_round_5_error=None,
        )
        logger.info("job_id=%s round5_feedback_done", job_id)
        return parsed
    except Exception as exc:
        message = _truncate(str(exc))
        job_store.update_job(
            job_id,
            feedback_round_5_status="failed",
            feedback_round_5_error=message,
        )
        logger.warning("job_id=%s round5_feedback_failed error=%s", job_id, message)
        raise
