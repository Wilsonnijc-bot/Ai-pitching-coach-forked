import json
import os
import re
import time
from typing import Callable, Optional

from google.api_core.client_options import ClientOptions
from google.cloud import speech_v2
from google.cloud.speech_v2.types import cloud_speech

from .gcs_utils import build_gs_uri, download_text, get_default_bucket, list_blobs, parse_gcs_uri
from .models import duration_to_seconds


StageCallback = Callable[[str, int], None]


def get_project_id() -> str:
    project_id = os.getenv("GCP_PROJECT_ID", "double-scholar-487115-b1").strip()
    if not project_id:
        raise RuntimeError("GCP_PROJECT_ID is not set.")
    return project_id


def get_speech_location() -> str:
    location = os.getenv("GCP_SPEECH_LOCATION", "us-central1").strip()
    if not location:
        raise RuntimeError("GCP_SPEECH_LOCATION is not set.")
    return location


def build_audio_blob_path(job_id: str) -> str:
    return f"jobs/{job_id}/audio.wav"


def build_output_prefix(job_id: str) -> str:
    return f"jobs/{job_id}/stt_v2_output/"


def build_output_uri(job_id: str, bucket: str) -> str:
    return build_gs_uri(bucket, build_output_prefix(job_id))


def _emit_stage(callback: Optional[StageCallback], status: str, progress: int) -> None:
    if callback is not None:
        callback(status, progress)


def _normalize_speaker_label(raw_speaker_label) -> Optional[str]:
    if raw_speaker_label is None:
        return None
    value = str(raw_speaker_label).strip()
    if not value:
        return None

    if value.isdigit():
        return f"spk{int(value)}"

    match = re.search(r"(\d+)", value)
    if match:
        return f"spk{int(match.group(1))}"

    return value


def _normalize_batch_results(batch_results: cloud_speech.BatchRecognizeResults) -> tuple[dict, bool]:
    full_text_parts: list[str] = []
    segments: list[dict] = []
    words: list[dict] = []
    has_speaker_tags = False

    for result in batch_results.results:
        if not result.alternatives:
            continue

        alternative = result.alternatives[0]
        transcript = (alternative.transcript or "").strip()
        if transcript:
            full_text_parts.append(transcript)

        alt_words = list(alternative.words or [])
        if alt_words:
            segment_start = duration_to_seconds(alt_words[0].start_offset)
            segment_end = duration_to_seconds(alt_words[-1].end_offset)
        else:
            segment_start = 0.0
            segment_end = duration_to_seconds(result.result_end_offset)

        segments.append(
            {
                "start": segment_start,
                "end": segment_end,
                "text": transcript,
            }
        )

        for word_info in alt_words:
            speaker = _normalize_speaker_label(getattr(word_info, "speaker_label", None))
            if speaker:
                has_speaker_tags = True
            words.append(
                {
                    "start": duration_to_seconds(word_info.start_offset),
                    "end": duration_to_seconds(word_info.end_offset),
                    "word": word_info.word,
                    "speaker": speaker,
                }
            )

    return (
        {
            "full_text": " ".join(full_text_parts).strip(),
            "segments": segments,
            "words": words,
        },
        has_speaker_tags,
    )


def _parse_batch_results_json(json_text: str) -> tuple[dict, bool]:
    try:
        batch_results = cloud_speech.BatchRecognizeResults.from_json(json_text)
    except Exception:
        # Fall back to raw JSON parsing for defensive handling.
        payload = json.loads(json_text)
        if isinstance(payload, dict):
            if "results" in payload:
                batch_results = cloud_speech.BatchRecognizeResults.from_json(json.dumps(payload))
            elif "transcript" in payload and isinstance(payload["transcript"], dict):
                batch_results = cloud_speech.BatchRecognizeResults.from_json(
                    json.dumps(payload["transcript"])
                )
            else:
                raise RuntimeError("Unexpected STT V2 JSON output structure.")
        else:
            raise RuntimeError("Unexpected non-object STT V2 JSON output.")
    return _normalize_batch_results(batch_results)


def _extract_result_blob_names(
    response: Optional[cloud_speech.BatchRecognizeResponse],
    bucket: str,
) -> list[str]:
    if response is None:
        return []

    blob_names: list[str] = []
    for file_result in response.results.values():
        cloud_result = file_result.cloud_storage_result
        if not cloud_result or not cloud_result.uri:
            continue
        result_bucket, blob_name = parse_gcs_uri(cloud_result.uri)
        if result_bucket == bucket:
            blob_names.append(blob_name)
    return sorted(set(blob_names))


def _merge_transcripts(results: list[dict], speaker_flags: list[bool]) -> tuple[dict, bool]:
    full_text_parts: list[str] = []
    segments: list[dict] = []
    words: list[dict] = []

    for result in results:
        text = str(result.get("full_text") or "").strip()
        if text:
            full_text_parts.append(text)
        segments.extend(result.get("segments", []))
        words.extend(result.get("words", []))

    return (
        {
            "full_text": " ".join(full_text_parts).strip(),
            "segments": segments,
            "words": words,
        },
        any(speaker_flags),
    )


def _read_results_from_gcs(
    *,
    job_id: str,
    bucket: str,
    response: Optional[cloud_speech.BatchRecognizeResponse],
) -> tuple[dict, bool]:
    output_prefix = build_output_prefix(job_id)
    last_errors: list[str] = []

    for _ in range(15):
        blob_names = set(_extract_result_blob_names(response, bucket))
        blob_names.update(list_blobs(output_prefix, bucket=bucket))
        ordered_blob_names = sorted(blob_names)

        if not ordered_blob_names:
            time.sleep(2)
            continue

        parsed_results: list[dict] = []
        parsed_has_speaker: list[bool] = []
        parse_errors: list[str] = []

        for blob_name in ordered_blob_names:
            if blob_name.endswith("/"):
                continue
            if not blob_name.lower().endswith(".json"):
                continue
            try:
                json_text = download_text(bucket, blob_name)
                parsed_result, has_speaker = _parse_batch_results_json(json_text)
                parsed_results.append(parsed_result)
                parsed_has_speaker.append(has_speaker)
            except Exception as exc:
                parse_errors.append(f"gs://{bucket}/{blob_name} -> {exc}")
                continue

        if parsed_results:
            return _merge_transcripts(parsed_results, parsed_has_speaker)

        last_errors = parse_errors
        time.sleep(2)

    if last_errors:
        raise RuntimeError(
            "Speech-to-Text V2 output parsing failed for all candidate files: "
            + "; ".join(last_errors[:3])
        )

    raise RuntimeError(
        f"Speech-to-Text V2 produced no parseable JSON output under gs://{bucket}/{output_prefix}"
    )


def transcribe_v2_chirp2_from_gcs(
    job_id: str,
    gcs_audio_uri: str,
    *,
    on_stage: Optional[StageCallback] = None,
) -> dict:
    project_id = get_project_id()
    location = get_speech_location()
    bucket = get_default_bucket()
    output_uri = build_output_uri(job_id, bucket)
    recognizer = f"projects/{project_id}/locations/{location}/recognizers/_"

    endpoint = f"{location}-speech.googleapis.com"
    client = speech_v2.SpeechClient(client_options=ClientOptions(api_endpoint=endpoint))
    def build_request(enable_diarization: bool) -> cloud_speech.BatchRecognizeRequest:
        features = cloud_speech.RecognitionFeatures(
            enable_automatic_punctuation=True,
            enable_word_time_offsets=True,
        )
        if enable_diarization:
            features.diarization_config = cloud_speech.SpeakerDiarizationConfig(
                min_speaker_count=1,
                max_speaker_count=2,
            )

        return cloud_speech.BatchRecognizeRequest(
            recognizer=recognizer,
            config=cloud_speech.RecognitionConfig(
                auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
                language_codes=["en-US"],
                model="chirp_2",
                features=features,
            ),
            files=[cloud_speech.BatchRecognizeFileMetadata(uri=gcs_audio_uri)],
            recognition_output_config=cloud_speech.RecognitionOutputConfig(
                gcs_output_config=cloud_speech.GcsOutputConfig(uri=output_uri)
            ),
        )

    _emit_stage(on_stage, "stt_batch_recognize", 40)
    diarization_requested = True
    try:
        operation = client.batch_recognize(request=build_request(enable_diarization=True))
        _emit_stage(on_stage, "waiting_for_stt", 60)
        response = operation.result(timeout=600)
    except Exception as first_exc:
        message = str(first_exc).lower()
        if "diarization" not in message and "speaker" not in message:
            raise RuntimeError(f"Speech-to-Text V2 BatchRecognize failed: {first_exc}") from first_exc

        # Fallback path: continue without diarization instead of failing whole job.
        diarization_requested = False
        _emit_stage(on_stage, "stt_batch_recognize", 40)
        try:
            operation = client.batch_recognize(request=build_request(enable_diarization=False))
            _emit_stage(on_stage, "waiting_for_stt", 60)
            response = operation.result(timeout=600)
        except Exception as second_exc:
            raise RuntimeError(
                "Speech-to-Text V2 BatchRecognize failed after retry without diarization: "
                f"{second_exc}"
            ) from second_exc

    _emit_stage(on_stage, "parsing_results", 80)
    transcript, has_diarization = _read_results_from_gcs(job_id=job_id, bucket=bucket, response=response)
    return {
        "transcript": transcript,
        "has_diarization": has_diarization,
        "diarization_requested": diarization_requested,
        "model": "chirp_2",
        "location": location,
        "bucket": bucket,
        "output_uri": output_uri,
    }
