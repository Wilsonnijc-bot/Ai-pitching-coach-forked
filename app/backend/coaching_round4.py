from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from .coaching_input import load_shared_input, SharedCoachingInput, WordTimestamp
from .gcs_utils import download_blob_to_file
from .llm_gptsapi import request_chat_completion
from .prompts.round4 import ROUND_4_VERSION, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from .storage import JobStore


logger = logging.getLogger("uvicorn.error")
MAX_ERROR_CHARS = 1200
EXPECTED_CRITERIA = {
    "Posture & Stillness",
    "Eye Contact",
    "Calm Confidence",
}


def _truncate(text: str, max_chars: int = MAX_ERROR_CHARS) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


def _recompute_body_language(job_store: JobStore, job_id: str) -> Optional[dict]:
    """Try to recompute body-language metrics by downloading the video from
    GCS and running MediaPipe analysis.  Returns the metrics dict on success
    or ``None`` on any failure."""
    from .video_metrics import BODY_LANGUAGE_AVAILABLE
    if not BODY_LANGUAGE_AVAILABLE:
        logger.error("job_id=%s cannot compute body language: mediapipe/opencv not installed", job_id)
        return None

    job = job_store.get_job(job_id)
    if not job:
        logger.warning("job_id=%s job not found for body language recomputation", job_id)
        return None

    video_gcs_uri = getattr(job, "video_gcs_uri", None) or ""
    if not video_gcs_uri:
        logger.error(
            "job_id=%s no video_gcs_uri stored — the video was never uploaded to GCS. "
            "This usually means the initial upload failed or used a code path that "
            "doesn't persist the video.",
            job_id,
        )
        return None

    # Parse gs://bucket/blob from the URI
    if not video_gcs_uri.startswith("gs://"):
        logger.warning("job_id=%s invalid video_gcs_uri=%s", job_id, video_gcs_uri)
        return None

    stripped = video_gcs_uri[len("gs://"):]
    slash_idx = stripped.find("/")
    if slash_idx <= 0:
        return None
    bucket = stripped[:slash_idx]
    blob_path = stripped[slash_idx + 1:]

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"r4_bl_{job_id}_"))
    suffix = Path(blob_path).suffix or ".webm"
    local_video = tmp_dir / f"video{suffix}"
    try:
        logger.info("job_id=%s downloading video from gs://%s/%s", job_id, bucket, blob_path)
        download_blob_to_file(bucket, blob_path, local_video)
        file_size = local_video.stat().st_size
        logger.info("job_id=%s downloaded video (%d bytes)", job_id, file_size)
        if file_size == 0:
            logger.error("job_id=%s downloaded video is empty (0 bytes)", job_id)
            return None

        from .video_metrics import compute_body_language_metrics

        # Load calibration data if available
        cal_data = getattr(job, "calibration_data", None)
        result = compute_body_language_metrics(local_video, calibration=cal_data)
        if result is None:
            logger.warning(
                "job_id=%s compute_body_language_metrics returned None — "
                "video may be unreadable or too short",
                job_id,
            )
        return result
    except FileNotFoundError:
        logger.error(
            "job_id=%s video blob not found in GCS (gs://%s/%s) — "
            "it may have been deleted or the upload never completed",
            job_id, bucket, blob_path,
        )
        return None
    except Exception:
        logger.error("job_id=%s body_language recomputation failed", job_id, exc_info=True)
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _parse_json(raw_content: str) -> dict:
    parsed = json.loads(raw_content)
    if not isinstance(parsed, dict):
        raise RuntimeError("Round 4 JSON root must be an object.")
    return parsed


def _validate_round4_schema(payload: dict) -> dict:
    if payload.get("round") != 4:
        raise RuntimeError('Round 4 payload must contain "round": 4.')
    if not isinstance(payload.get("title"), str) or not str(payload.get("title")).strip():
        raise RuntimeError('Round 4 payload must contain a non-empty "title" string.')

    sections = payload.get("sections")
    if not isinstance(sections, list) or len(sections) != 3:
        raise RuntimeError('Round 4 payload must contain exactly 3 "sections".')

    body_actions = payload.get("top_3_body_language_actions")
    if not isinstance(body_actions, list):
        raise RuntimeError('Round 4 payload must include "top_3_body_language_actions" as an array.')

    seen_criteria: set[str] = set()
    for section in sections:
        if not isinstance(section, dict):
            raise RuntimeError("Each round 4 section must be an object.")

        criterion = str(section.get("criterion") or "").strip()
        verdict = str(section.get("verdict") or "").strip().lower()
        if criterion not in EXPECTED_CRITERIA:
            raise RuntimeError(f'Unexpected round 4 criterion "{criterion}".')
        if verdict not in {"strong", "mixed", "weak"}:
            raise RuntimeError(f'Invalid verdict "{verdict}" in section "{criterion}".')
        seen_criteria.add(criterion)

        # Section-specific validation
        if criterion == "Posture & Stillness":
            if not isinstance(section.get("overall_assessment"), str):
                raise RuntimeError('"overall_assessment" must be a string in Posture & Stillness.')
            if not isinstance(section.get("stable_moments"), list):
                raise RuntimeError('"stable_moments" must be an array.')
            # Validate sentence_text in each stable moment (string or null)
            for sm in section.get("stable_moments", []):
                if isinstance(sm, dict) and "sentence_text" in sm:
                    st = sm["sentence_text"]
                    if st is not None and not isinstance(st, str):
                        raise RuntimeError('"sentence_text" in stable_moments must be a string or null.')
            if not isinstance(section.get("unstable_moments"), list):
                raise RuntimeError('"unstable_moments" must be an array.')

        elif criterion == "Eye Contact":
            if not isinstance(section.get("overall_assessment"), str):
                raise RuntimeError('"overall_assessment" must be a string in Eye Contact.')
            if not isinstance(section.get("strong_eye_contact_moments"), list):
                raise RuntimeError('"strong_eye_contact_moments" must be an array.')
            if not isinstance(section.get("look_away_moments"), list):
                raise RuntimeError('"look_away_moments" must be an array.')

        elif criterion == "Calm Confidence":
            if not isinstance(section.get("overall_assessment"), str):
                raise RuntimeError('"overall_assessment" must be a string in Calm Confidence.')
            if not isinstance(section.get("confident_moments"), list):
                raise RuntimeError('"confident_moments" must be an array.')
            # Validate sentence_text in confident moments (string or null)
            for cm in section.get("confident_moments", []):
                if isinstance(cm, dict) and "sentence_text" in cm:
                    st = cm["sentence_text"]
                    if st is not None and not isinstance(st, str):
                        raise RuntimeError('"sentence_text" in confident_moments must be a string or null.')
            if not isinstance(section.get("turned_away_events"), list):
                raise RuntimeError('"turned_away_events" must be an array.')
            # Validate sentence_text in turned away events (string or null)
            for ta in section.get("turned_away_events", []):
                if isinstance(ta, dict) and "sentence_text" in ta:
                    st = ta["sentence_text"]
                    if st is not None and not isinstance(st, str):
                        raise RuntimeError('"sentence_text" in turned_away_events must be a string or null.')
            if not isinstance(section.get("why_facing_matters"), str):
                raise RuntimeError('"why_facing_matters" must be a string.')
            if not isinstance(section.get("recommended_stance_adjustments"), list):
                raise RuntimeError('"recommended_stance_adjustments" must be an array.')

    if seen_criteria != EXPECTED_CRITERIA:
        raise RuntimeError("Round 4 sections do not match required criteria.")

    return payload


# ---------------------------------------------------------------------------
# Transcript alignment helpers
# ---------------------------------------------------------------------------

def _parse_time_range(time_range: str) -> tuple[float, float] | None:
    """Parse a time range string like '0:00–0:10' or '1:30–2:00' into
    (start_sec, end_sec).  Returns None if parsing fails."""
    # Normalise various dash characters
    tr = time_range.replace("\u2013", "-").replace("\u2014", "-").strip()
    m = re.match(r"(\d+):(\d{1,2}(?:\.\d+)?)\s*-\s*(\d+):(\d{1,2}(?:\.\d+)?)", tr)
    if not m:
        return None
    start = int(m.group(1)) * 60 + float(m.group(2))
    end = int(m.group(3)) * 60 + float(m.group(4))
    return (start, end)


def _extract_sentence_for_window(
    words: list[WordTimestamp],
    start_sec: float,
    end_sec: float,
) -> str | None:
    """Return the transcript text spoken during [start_sec, end_sec].

    Selects all words whose time ranges overlap the window and joins them.
    If no words overlap, returns None.
    """
    if not words:
        return None
    overlapping = [
        w for w in words
        if w.end > start_sec and w.start < end_sec
    ]
    if not overlapping:
        return None
    text = " ".join(w.word for w in overlapping).strip()
    return text if text else None


def _backfill_sentence_text(parsed: dict, words: list[WordTimestamp]) -> dict:
    """For each moment card across all Round 4 sections, populate
    sentence_text from the actual transcript word timestamps.  This ensures
    accuracy even if the LLM hallucinated or omitted the text."""
    # Map: criterion -> list of moment-array keys to backfill
    _MOMENT_KEYS_BY_CRITERION: dict[str, list[str]] = {
        "Posture & Stillness": ["stable_moments", "unstable_moments"],
        "Eye Contact": ["strong_eye_contact_moments", "look_away_moments"],
        "Calm Confidence": ["confident_moments", "turned_away_events"],
    }

    for section in parsed.get("sections", []):
        criterion = section.get("criterion", "")
        moment_keys = _MOMENT_KEYS_BY_CRITERION.get(criterion, [])
        for key in moment_keys:
            for moment in section.get(key, []):
                tr = moment.get("time_range", "")
                parsed_range = _parse_time_range(tr)
                if parsed_range is None:
                    if "sentence_text" not in moment:
                        moment["sentence_text"] = None
                    continue
                start, end = parsed_range
                extracted = _extract_sentence_for_window(words, start, end)
                moment["sentence_text"] = extracted  # override with ground truth
    return parsed


def _build_round4_user_prompt(shared_input: SharedCoachingInput) -> str:
    derived = shared_input.derived_metrics.model_dump()
    body_language = derived.get("body_language") or {}

    posture_timeline = body_language.get("posture_timeline") or []
    eye_contact_timeline = body_language.get("eye_contact_timeline") or []
    facing_timeline = body_language.get("facing_timeline") or []
    unstable_events = body_language.get("unstable_events") or []
    look_away_events = body_language.get("look_away_events") or []
    turned_away_events = body_language.get("turned_away_events") or []
    summary = body_language.get("summary") or {}

    return (
        USER_PROMPT_TEMPLATE
        .replace("{body_language_summary_json}", json.dumps(summary, ensure_ascii=False))
        .replace("{posture_timeline_json}", json.dumps(posture_timeline, ensure_ascii=False))
        .replace("{unstable_events_json}", json.dumps(unstable_events, ensure_ascii=False))
        .replace("{eye_contact_timeline_json}", json.dumps(eye_contact_timeline, ensure_ascii=False))
        .replace("{look_away_events_json}", json.dumps(look_away_events, ensure_ascii=False))
        .replace("{facing_timeline_json}", json.dumps(facing_timeline, ensure_ascii=False))
        .replace("{turned_away_events_json}", json.dumps(turned_away_events, ensure_ascii=False))
        .replace("{transcript_full_text}", shared_input.transcript_full_text)
        .replace("{deck_text_or_empty}", shared_input.deck_text or "")
    )


def _request_round4_output(user_prompt: str) -> str:
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


def run_round4(job_store: JobStore, job_id: str) -> dict:
    job_store.update_job(
        job_id,
        feedback_round_4_status="running",
        feedback_round_4_error=None,
        feedback_round_4_version=ROUND_4_VERSION,
    )

    try:
        shared_input = load_shared_input(job_store, job_id)

        # Check that body language data is available; recompute on demand
        # from the GCS-stored video if missing.
        derived = shared_input.derived_metrics.model_dump()
        body_language = derived.get("body_language")
        if not body_language:
            logger.info("job_id=%s round4 body_language missing, attempting recomputation", job_id)
            body_language = _recompute_body_language(job_store, job_id)
            if body_language:
                # Persist so future rounds don't need to recompute
                derived["body_language"] = body_language
                job_store.update_job(job_id, derived_metrics=derived)
                shared_input.derived_metrics = shared_input.derived_metrics.model_copy(
                    update={"body_language": body_language}
                )
                logger.info("job_id=%s round4 body_language recomputed successfully", job_id)
            else:
                raise RuntimeError(
                    "Body language metrics could not be computed for this job. "
                    "The video may not have been uploaded, was too short, "
                    "or required libraries (mediapipe/opencv) are unavailable."
                )

        user_prompt = _build_round4_user_prompt(shared_input)
        raw_output = _request_round4_output(user_prompt)

        try:
            parsed = _validate_round4_schema(_parse_json(raw_output))
        except Exception:
            repaired_output = _request_round4_output(_repair_prompt(raw_output))
            parsed = _validate_round4_schema(_parse_json(repaired_output))

        # Backfill sentence_text from actual transcript word timestamps
        # (overrides any LLM-generated text with ground truth)
        parsed = _backfill_sentence_text(parsed, shared_input.words)

        job_store.update_job(
            job_id,
            feedback_round_4=parsed,
            feedback_round_4_version=ROUND_4_VERSION,
            feedback_round_4_status="done",
            feedback_round_4_error=None,
        )
        logger.info("job_id=%s round4_feedback_done", job_id)
        return parsed
    except Exception as exc:
        message = _truncate(str(exc))
        job_store.update_job(
            job_id,
            feedback_round_4_status="failed",
            feedback_round_4_error=message,
        )
        logger.warning("job_id=%s round4_feedback_failed error=%s", job_id, message)
        raise
