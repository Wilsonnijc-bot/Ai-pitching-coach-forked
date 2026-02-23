import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from google.api_core.client_options import ClientOptions
from google.cloud import speech_v2
from google.cloud.speech_v2.types import cloud_speech

from .gcp_auth import get_gcp_credentials
from .gcs_utils import build_gs_uri, download_text, get_default_bucket, list_blobs, parse_gcs_uri
from .models import duration_to_seconds


StageCallback = Callable[[str, int], None]

STT_PARALLEL_CHUNK_COUNT = 4
WORD_DEDUPE_EPSILON_SEC = 0.12
SPEAKER_TIME_BUCKET_SEC = 0.2


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    value = os.getenv(name)
    if value is None:
        return max(minimum, default)
    try:
        parsed = int(value)
    except ValueError:
        return max(minimum, default)
    return max(minimum, parsed)


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


def build_chunk_output_root_prefix(job_id: str) -> str:
    return f"jobs/{job_id}/stt_v2_output_chunks/"


def build_chunk_output_prefix(job_id: str, chunk_index: int) -> str:
    return f"{build_chunk_output_root_prefix(job_id)}chunk_{chunk_index + 1}/"


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


def _normalize_token(text: str) -> str:
    token = str(text or "").strip().lower()
    token = re.sub(r"^[\W_]+|[\W_]+$", "", token)
    return token


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
    output_prefix: str,
    bucket: str,
    response: Optional[cloud_speech.BatchRecognizeResponse],
) -> tuple[dict, bool]:
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


def _build_speech_client(location: str) -> speech_v2.SpeechClient:
    endpoint = f"{location}-speech.googleapis.com"
    credentials = get_gcp_credentials()
    if credentials is not None:
        return speech_v2.SpeechClient(
            client_options=ClientOptions(api_endpoint=endpoint),
            credentials=credentials,
        )
    return speech_v2.SpeechClient(client_options=ClientOptions(api_endpoint=endpoint))


def _build_batch_request(
    *,
    recognizer: str,
    gcs_audio_uri: str,
    output_uri: str,
    enable_diarization: bool,
) -> cloud_speech.BatchRecognizeRequest:
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


def _run_single_file_transcription(
    *,
    gcs_audio_uri: str,
    output_prefix: str,
    project_id: str,
    location: str,
    bucket: str,
    emit_stages: bool,
    on_stage: Optional[StageCallback],
) -> dict:
    output_uri = build_gs_uri(bucket, output_prefix)
    recognizer = f"projects/{project_id}/locations/{location}/recognizers/_"
    client = _build_speech_client(location)

    if emit_stages:
        _emit_stage(on_stage, "stt_batch_recognize", 40)

    diarization_requested = True
    try:
        operation = client.batch_recognize(
            request=_build_batch_request(
                recognizer=recognizer,
                gcs_audio_uri=gcs_audio_uri,
                output_uri=output_uri,
                enable_diarization=True,
            )
        )
        if emit_stages:
            _emit_stage(on_stage, "waiting_for_stt", 60)
        response = operation.result(timeout=600)
    except Exception as first_exc:
        message = str(first_exc).lower()
        if "diarization" not in message and "speaker" not in message:
            raise RuntimeError(f"Speech-to-Text V2 BatchRecognize failed: {first_exc}") from first_exc

        # Fallback path: continue without diarization instead of failing whole job.
        diarization_requested = False
        if emit_stages:
            _emit_stage(on_stage, "stt_batch_recognize", 40)
        try:
            operation = client.batch_recognize(
                request=_build_batch_request(
                    recognizer=recognizer,
                    gcs_audio_uri=gcs_audio_uri,
                    output_uri=output_uri,
                    enable_diarization=False,
                )
            )
            if emit_stages:
                _emit_stage(on_stage, "waiting_for_stt", 60)
            response = operation.result(timeout=600)
        except Exception as second_exc:
            raise RuntimeError(
                "Speech-to-Text V2 BatchRecognize failed after retry without diarization: "
                f"{second_exc}"
            ) from second_exc

    if emit_stages:
        _emit_stage(on_stage, "parsing_results", 80)
    transcript, has_diarization = _read_results_from_gcs(
        output_prefix=output_prefix,
        bucket=bucket,
        response=response,
    )
    return {
        "transcript": transcript,
        "has_diarization": has_diarization,
        "diarization_requested": diarization_requested,
        "model": "chirp_2",
        "location": location,
        "bucket": bucket,
        "output_uri": output_uri,
    }


def _normalize_chunk_words(words: list[dict], chunk_index: int, start_offset_sec: float) -> list[dict]:
    normalized: list[dict] = []
    for word in words:
        token = str(word.get("word") or "").strip()
        if not token:
            continue
        start = float(word.get("start") or 0.0) + start_offset_sec
        end = float(word.get("end") or 0.0) + start_offset_sec
        speaker = _normalize_speaker_label(word.get("speaker"))
        normalized.append(
            {
                "start": max(0.0, start),
                "end": max(0.0, end),
                "word": token,
                "speaker": speaker,
                "_chunk_index": chunk_index,
            }
        )
    return normalized


def _normalize_chunk_segments(segments: list[dict], chunk_index: int, start_offset_sec: float) -> list[dict]:
    normalized: list[dict] = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start") or 0.0) + start_offset_sec
        end = float(segment.get("end") or 0.0) + start_offset_sec
        normalized.append(
            {
                "start": max(0.0, start),
                "end": max(0.0, end),
                "text": text,
                "_chunk_index": chunk_index,
            }
        )
    return normalized


def _time_bucket_key(value: float) -> int:
    return int(round(value / SPEAKER_TIME_BUCKET_SEC))


def _remap_speakers_across_chunks(words_by_chunk: list[list[dict]], chunk_specs: list[dict]) -> None:
    next_speaker_id = 1
    known_global_speakers: set[str] = set()

    def allocate_global() -> str:
        nonlocal next_speaker_id
        while f"spk{next_speaker_id}" in known_global_speakers:
            next_speaker_id += 1
        label = f"spk{next_speaker_id}"
        known_global_speakers.add(label)
        next_speaker_id += 1
        return label

    for chunk_index, words in enumerate(words_by_chunk):
        local_speakers: list[str] = []
        for word in words:
            speaker = _normalize_speaker_label(word.get("speaker"))
            word["speaker"] = speaker
            if speaker and speaker not in local_speakers:
                local_speakers.append(speaker)

        if not local_speakers:
            continue

        mapping: dict[str, str] = {}
        used_globals: set[str] = set()

        if chunk_index > 0:
            prev_words = words_by_chunk[chunk_index - 1]
            prev_spec = chunk_specs[chunk_index - 1]
            curr_spec = chunk_specs[chunk_index]
            overlap_start = max(float(prev_spec["start_sec"]), float(curr_spec["start_sec"]))
            overlap_end = min(float(prev_spec["end_sec"]), float(curr_spec["end_sec"]))

            prev_lookup: dict[tuple[str, int, int], list[str]] = {}
            for word in prev_words:
                global_speaker = word.get("speaker")
                if not global_speaker:
                    continue
                start = float(word.get("start") or 0.0)
                end = float(word.get("end") or 0.0)
                if end < overlap_start or start > overlap_end:
                    continue
                token = _normalize_token(word.get("word") or "")
                if not token:
                    continue
                key = (token, _time_bucket_key(start), _time_bucket_key(end))
                prev_lookup.setdefault(key, []).append(global_speaker)

            votes: dict[tuple[str, str], int] = {}
            for word in words:
                local = word.get("speaker")
                if not local:
                    continue
                start = float(word.get("start") or 0.0)
                end = float(word.get("end") or 0.0)
                if end < overlap_start or start > overlap_end:
                    continue
                token = _normalize_token(word.get("word") or "")
                if not token:
                    continue
                key = (token, _time_bucket_key(start), _time_bucket_key(end))
                for global_speaker in prev_lookup.get(key, []):
                    votes[(local, global_speaker)] = votes.get((local, global_speaker), 0) + 1

            ranked_votes = sorted(votes.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))
            for (local, global_speaker), _score in ranked_votes:
                if local in mapping:
                    continue
                if global_speaker in used_globals:
                    continue
                mapping[local] = global_speaker
                used_globals.add(global_speaker)

        for local in local_speakers:
            if local in mapping:
                continue
            if local in known_global_speakers and local not in used_globals:
                mapping[local] = local
                used_globals.add(local)

        for local in local_speakers:
            if local in mapping:
                continue
            mapping[local] = allocate_global()

        for word in words:
            local = word.get("speaker")
            if local:
                word["speaker"] = mapping.get(local, local)

        known_global_speakers.update(mapping.values())


def _merge_chunk_words(words_by_chunk: list[list[dict]]) -> list[dict]:
    combined: list[dict] = []
    for words in words_by_chunk:
        combined.extend(words)

    combined.sort(
        key=lambda item: (
            float(item.get("start") or 0.0),
            float(item.get("end") or 0.0),
            int(item.get("_chunk_index") or 0),
            str(item.get("word") or ""),
        )
    )

    merged: list[dict] = []
    for word in combined:
        token = _normalize_token(word.get("word") or "")
        start = float(word.get("start") or 0.0)
        end = float(word.get("end") or 0.0)

        duplicate_found = False
        if token:
            for existing in reversed(merged):
                existing_start = float(existing.get("start") or 0.0)
                if (start - existing_start) > WORD_DEDUPE_EPSILON_SEC:
                    break
                existing_token = _normalize_token(existing.get("word") or "")
                if token != existing_token:
                    continue
                if abs(start - existing_start) > WORD_DEDUPE_EPSILON_SEC:
                    continue
                existing_end = float(existing.get("end") or 0.0)
                if abs(end - existing_end) > WORD_DEDUPE_EPSILON_SEC:
                    continue
                duplicate_found = True
                break

        if duplicate_found:
            continue
        merged.append(word)

    clean_words: list[dict] = []
    for word in merged:
        clean_words.append(
            {
                "start": float(word.get("start") or 0.0),
                "end": float(word.get("end") or 0.0),
                "word": str(word.get("word") or ""),
                "speaker": word.get("speaker"),
            }
        )
    return clean_words


def _merge_chunk_segments(segments_by_chunk: list[list[dict]], chunk_specs: list[dict]) -> list[dict]:
    selected: list[dict] = []
    for chunk_index, segments in enumerate(segments_by_chunk):
        spec = chunk_specs[chunk_index]
        core_start = float(spec["core_start_sec"])
        core_end = float(spec["core_end_sec"])
        for segment in segments:
            start = float(segment.get("start") or 0.0)
            end = float(segment.get("end") or 0.0)
            midpoint = (start + end) / 2.0
            if midpoint < (core_start - WORD_DEDUPE_EPSILON_SEC):
                continue
            if midpoint > (core_end + WORD_DEDUPE_EPSILON_SEC):
                continue
            selected.append(segment)

    if not selected:
        for segments in segments_by_chunk:
            selected.extend(segments)

    selected.sort(
        key=lambda item: (
            float(item.get("start") or 0.0),
            float(item.get("end") or 0.0),
            int(item.get("_chunk_index") or 0),
        )
    )

    clean_segments: list[dict] = []
    for segment in selected:
        clean_segments.append(
            {
                "start": float(segment.get("start") or 0.0),
                "end": float(segment.get("end") or 0.0),
                "text": str(segment.get("text") or ""),
            }
        )
    return clean_segments


def transcribe_v2_chirp2_from_gcs(
    job_id: str,
    gcs_audio_uri: str,
    *,
    on_stage: Optional[StageCallback] = None,
) -> dict:
    project_id = get_project_id()
    location = get_speech_location()
    bucket = get_default_bucket()
    output_prefix = build_output_prefix(job_id)
    return _run_single_file_transcription(
        gcs_audio_uri=gcs_audio_uri,
        output_prefix=output_prefix,
        project_id=project_id,
        location=location,
        bucket=bucket,
        emit_stages=True,
        on_stage=on_stage,
    )


def transcribe_v2_chirp2_from_gcs_chunks(
    job_id: str,
    chunk_specs: list[dict],
    *,
    max_workers: Optional[int] = None,
    on_stage: Optional[StageCallback] = None,
) -> dict:
    if len(chunk_specs) != STT_PARALLEL_CHUNK_COUNT:
        raise RuntimeError(
            f"Chunked STT requires exactly {STT_PARALLEL_CHUNK_COUNT} chunk specs; got {len(chunk_specs)}."
        )

    for index, spec in enumerate(chunk_specs):
        for required in ("uri", "start_sec", "end_sec", "core_start_sec", "core_end_sec"):
            if required not in spec:
                raise RuntimeError(f"Chunk spec {index + 1} missing required field '{required}'.")

    project_id = get_project_id()
    location = get_speech_location()
    bucket = get_default_bucket()

    _emit_stage(on_stage, "stt_batch_recognize", 40)
    _emit_stage(on_stage, "waiting_for_stt", 60)

    configured_workers = (
        _int_env("STT_PARALLEL_CHUNK_MAX_WORKERS", 4)
        if max_workers is None
        else max(1, int(max_workers))
    )
    worker_count = max(1, min(configured_workers, STT_PARALLEL_CHUNK_COUNT))
    chunk_results: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {}
        for index, spec in enumerate(chunk_specs):
            future = executor.submit(
                _run_single_file_transcription,
                gcs_audio_uri=str(spec["uri"]),
                output_prefix=build_chunk_output_prefix(job_id, index),
                project_id=project_id,
                location=location,
                bucket=bucket,
                emit_stages=False,
                on_stage=None,
            )
            future_map[future] = index

        for future in as_completed(future_map):
            index = future_map[future]
            try:
                chunk_results[index] = future.result()
            except Exception as exc:
                raise RuntimeError(f"Chunk {index + 1} transcription failed: {exc}") from exc

    _emit_stage(on_stage, "parsing_results", 80)

    words_by_chunk: list[list[dict]] = []
    segments_by_chunk: list[list[dict]] = []
    diarization_requested_flags: list[bool] = []

    for index, spec in enumerate(chunk_specs):
        payload = chunk_results.get(index)
        if payload is None:
            raise RuntimeError(f"Chunk {index + 1} result missing.")
        transcript = payload.get("transcript")
        if not isinstance(transcript, dict):
            raise RuntimeError(f"Chunk {index + 1} transcript payload is invalid.")

        words_by_chunk.append(
            _normalize_chunk_words(
                list(transcript.get("words") or []),
                chunk_index=index,
                start_offset_sec=float(spec["start_sec"]),
            )
        )
        segments_by_chunk.append(
            _normalize_chunk_segments(
                list(transcript.get("segments") or []),
                chunk_index=index,
                start_offset_sec=float(spec["start_sec"]),
            )
        )
        diarization_requested_flags.append(bool(payload.get("diarization_requested", True)))

    _remap_speakers_across_chunks(words_by_chunk, chunk_specs)
    merged_words = _merge_chunk_words(words_by_chunk)
    merged_segments = _merge_chunk_segments(segments_by_chunk, chunk_specs)
    merged_full_text = " ".join(
        segment_text
        for segment_text in (str(seg.get("text") or "").strip() for seg in merged_segments)
        if segment_text
    ).strip()

    has_diarization = any(bool(word.get("speaker")) for word in merged_words)
    diarization_requested = all(diarization_requested_flags) if diarization_requested_flags else True

    return {
        "transcript": {
            "full_text": merged_full_text,
            "segments": merged_segments,
            "words": merged_words,
        },
        "has_diarization": has_diarization,
        "diarization_requested": diarization_requested,
        "model": "chirp_2",
        "location": location,
        "bucket": bucket,
        "output_uri": build_gs_uri(bucket, build_chunk_output_root_prefix(job_id)),
    }
