"""Body-language metrics computed from video frames using MediaPipe.

Extracts frames every 0.5 s and runs Pose + Face Mesh to derive three
metric timelines:

1. **Posture (Shoulder Stability)** – tracks left/right shoulder positions
   and flags windows where either deviates from a rolling baseline.
2. **Eye Contact (Gaze Direction)** – uses iris landmarks relative to eye
   corners to estimate whether the speaker is looking at the camera.
3. **Calm Confidence (Facing Forward)** – head yaw + torso orientation to
   detect when the speaker turns away for extended periods.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("uvicorn.error")

# ---------------------------------------------------------------------------
# Dependency availability — checked at import time so server logs show clearly
# ---------------------------------------------------------------------------
_CV2_AVAILABLE = False
_MEDIAPIPE_AVAILABLE = False

try:
    import cv2  # noqa: F401

    _CV2_AVAILABLE = True
except ImportError:
    logger.warning(
        "opencv-python (cv2) is not installed — body language analysis will be "
        "unavailable. Install with: pip install opencv-contrib-python"
    )

try:
    import mediapipe  # noqa: F401

    _MEDIAPIPE_AVAILABLE = True
except ImportError:
    logger.warning(
        "mediapipe is not installed — body language analysis will be "
        "unavailable. Install with: pip install 'mediapipe>=0.10'"
    )

BODY_LANGUAGE_AVAILABLE = _CV2_AVAILABLE and _MEDIAPIPE_AVAILABLE


# ---------------------------------------------------------------------------
# Configuration constants (defaults — overridden by calibration when available)
# ---------------------------------------------------------------------------
SAMPLE_INTERVAL_SEC = 0.5  # extract one frame every 0.5 s
SHOULDER_DEVIATION_THRESHOLD = 0.035  # normalised Y deviation from baseline
ROLLING_BASELINE_WINDOW = 10  # number of frames for rolling average (5 s)
IRIS_CENTER_LOW = 0.35  # iris ratio thresholds for "looking at camera"
IRIS_CENTER_HIGH = 0.65
IRIS_TOLERANCE = 0.15  # +- tolerance around calibrated iris centre
HEAD_YAW_THRESHOLD_DEG = 25.0  # degrees; beyond => "turned away"
TURNED_AWAY_MIN_DURATION_SEC = 3.0  # consecutive turned-away before flagging


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(minimum, parsed)


def _float_env(name: str, default: float, minimum: float = 0.0) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(minimum, parsed)


BODY_LANGUAGE_PARALLEL_CHUNKS_ENABLED = _bool_env(
    "BODY_LANGUAGE_PARALLEL_CHUNKS_ENABLED", False
)
BODY_LANGUAGE_PARALLEL_CHUNKS_COUNT = 3
BODY_LANGUAGE_CHUNK_OVERLAP_SECONDS = _float_env(
    "BODY_LANGUAGE_CHUNK_OVERLAP_SECONDS", 5.0
)
BODY_LANGUAGE_CHUNK_MIN_VIDEO_SECONDS = _float_env(
    "BODY_LANGUAGE_CHUNK_MIN_VIDEO_SECONDS", 120.0
)
BODY_LANGUAGE_CHUNK_MAX_WORKERS = _int_env(
    "BODY_LANGUAGE_CHUNK_MAX_WORKERS", 3
)
TIMESTAMP_EPSILON_SEC = 1e-4


@dataclass
class _RawSignals:
    frame_indices: list[int]
    timestamps: list[float]
    left_shoulder_ys: list[float]
    right_shoulder_ys: list[float]
    shoulder_diffs: list[float]
    iris_ratios: list[Optional[float]]
    head_yaws: list[Optional[float]]
    facing_camera: list[bool]


# ---------------------------------------------------------------------------
# Calibration and math helpers
# ---------------------------------------------------------------------------

def _build_thresholds(calibration: Optional[dict]) -> dict:
    """Compute per-session thresholds from calibration data.

    If *calibration* is ``None`` the returned dict contains the module-level
    defaults, so all downstream code can just read from the dict.
    """
    t: dict = {
        "iris_center_low": IRIS_CENTER_LOW,
        "iris_center_high": IRIS_CENTER_HIGH,
        "shoulder_deviation_threshold": SHOULDER_DEVIATION_THRESHOLD,
        "head_yaw_threshold_deg": HEAD_YAW_THRESHOLD_DEG,
        "shoulder_natural_diff": 0.0,
        "head_yaw_offset_deg": 0.0,
    }
    if not calibration:
        return t

    iris_baseline = calibration.get("iris_baseline_ratio")
    if iris_baseline is not None:
        t["iris_center_low"] = max(0.0, iris_baseline - IRIS_TOLERANCE)
        t["iris_center_high"] = min(1.0, iris_baseline + IRIS_TOLERANCE)
        logger.info(
            "Calibrated iris thresholds: %.3f – %.3f (baseline=%.3f)",
            t["iris_center_low"],
            t["iris_center_high"],
            iris_baseline,
        )

    natural_diff = calibration.get("shoulder_baseline_diff")
    if natural_diff is not None and natural_diff > 0:
        t["shoulder_deviation_threshold"] = SHOULDER_DEVIATION_THRESHOLD + natural_diff
        t["shoulder_natural_diff"] = natural_diff
        logger.info(
            "Calibrated shoulder threshold: %.4f (natural_diff=%.4f)",
            t["shoulder_deviation_threshold"],
            natural_diff,
        )

    yaw_offset = calibration.get("head_yaw_baseline_deg")
    if yaw_offset is not None:
        t["head_yaw_offset_deg"] = yaw_offset
        logger.info("Calibrated head yaw offset: %.1f°", yaw_offset)

    return t


def _rolling_mean(values: list[float], window: int) -> list[float]:
    """Simple rolling mean; pads the first *window-1* entries with the
    cumulative mean so far."""
    out: list[float] = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i < window:
            out.append(total / (i + 1))
        else:
            total -= values[i - window]
            out.append(total / window)
    return out


def _iris_horizontal_ratio(
    iris_center_x: float,
    eye_inner_x: float,
    eye_outer_x: float,
) -> float:
    """Return 0-1 ratio of where the iris sits between inner (0) and outer (1)
    corners of the eye. ~0.5 => looking straight ahead."""
    span = abs(eye_outer_x - eye_inner_x)
    if span < 1e-6:
        return 0.5
    return (iris_center_x - min(eye_inner_x, eye_outer_x)) / span


def _head_yaw_from_face_landmarks(face_landmarks) -> float:
    """Estimate yaw angle (degrees) from face mesh landmarks.

    Uses the nose tip (#1) and left/right ear tragion approximations
    (#234 left side, #454 right side). Positive yaw = turned right.
    """
    nose = face_landmarks[1]
    left = face_landmarks[234]
    right = face_landmarks[454]

    d_left = abs(nose.x - left.x)
    d_right = abs(nose.x - right.x)

    total = d_left + d_right
    if total < 1e-6:
        return 0.0

    ratio = (d_right - d_left) / total
    yaw_rad = math.asin(max(-1.0, min(1.0, ratio)))
    return math.degrees(yaw_rad)


def _format_ts(sec: float) -> str:
    """Format seconds as M:SS.s for human-readable timelines."""
    m = int(sec) // 60
    s = sec - m * 60
    return f"{m}:{s:04.1f}"


# ---------------------------------------------------------------------------
# Codec fallback: .webm -> .mp4 via system ffmpeg
# ---------------------------------------------------------------------------

def _convert_webm_to_mp4(src: str | Path) -> Optional[Path]:
    """Convert *src* to an H.264 .mp4 in a temp directory.

    Returns the path to the new file, or ``None`` if ffmpeg is missing or
    the conversion fails. The caller is responsible for cleaning up the
    temp directory (``mp4_path.parent``).
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning("ffmpeg not on PATH — cannot convert video for body-language analysis")
        return None

    tmp_dir = Path(tempfile.mkdtemp(prefix="bl_conv_"))
    mp4_path = tmp_dir / "converted.mp4"

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-an",
        "-movflags",
        "+faststart",
        str(mp4_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            stderr_tail = (result.stderr or "").strip().splitlines()[-3:]
            logger.warning(
                "ffmpeg webm→mp4 conversion failed (rc=%d): %s",
                result.returncode,
                " | ".join(stderr_tail),
            )
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return None
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg conversion timed out (120 s)")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None
    except Exception:
        logger.warning("ffmpeg conversion error", exc_info=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None

    if not mp4_path.exists() or mp4_path.stat().st_size == 0:
        logger.warning("ffmpeg produced empty mp4 output")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None

    logger.info("Converted %s → %s (%d KB)", src, mp4_path, mp4_path.stat().st_size // 1024)
    return mp4_path


# ---------------------------------------------------------------------------
# Stage A: sampled signal extraction
# ---------------------------------------------------------------------------

def _prepare_video_source(video_path: str | Path) -> tuple[Optional[str], Optional[Path], dict]:
    import cv2

    source_path = str(video_path)
    converted_mp4: Optional[Path] = None

    cap = cv2.VideoCapture(source_path)
    if not cap.isOpened():
        logger.info(
            "cv2.VideoCapture could not open %s — attempting ffmpeg conversion to mp4",
            source_path,
        )
        converted_mp4 = _convert_webm_to_mp4(source_path)
        if converted_mp4 is not None:
            source_path = str(converted_mp4)
            cap = cv2.VideoCapture(source_path)

    if not cap.isOpened():
        logger.warning("Could not open video for body-language analysis: %s", video_path)
        if converted_mp4 is not None:
            shutil.rmtree(converted_mp4.parent, ignore_errors=True)
        return None, None, {}

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = max(1, int(round(fps * SAMPLE_INTERVAL_SEC)))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration_sec = (total_frames / fps) if total_frames > 0 else 0.0
    cap.release()

    meta = {
        "fps": fps,
        "frame_interval": frame_interval,
        "total_frames": total_frames,
        "duration_sec": duration_sec,
    }
    return source_path, converted_mp4, meta


def _extract_signals_for_range(
    video_path: str,
    *,
    thresholds: dict,
    fps: float,
    frame_interval: int,
    start_frame: int = 0,
    end_frame: Optional[int] = None,
) -> Optional[_RawSignals]:
    import cv2
    import mediapipe as mp

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.warning("Failed to open video for extraction: %s", video_path)
        return None

    start_frame = max(0, int(start_frame))
    if start_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, float(start_frame))

    current_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES) or 0)
    if current_frame < start_frame:
        while current_frame < start_frame:
            if not cap.grab():
                break
            current_frame += 1
    frame_idx = max(start_frame, current_frame)

    yaw_threshold = thresholds["head_yaw_threshold_deg"]
    yaw_offset = thresholds["head_yaw_offset_deg"]

    frame_indices: list[int] = []
    timestamps: list[float] = []
    left_shoulder_ys: list[float] = []
    right_shoulder_ys: list[float] = []
    shoulder_diffs: list[float] = []
    iris_ratios: list[Optional[float]] = []
    head_yaws: list[Optional[float]] = []
    facing_camera: list[bool] = []

    mp_pose = mp.solutions.pose
    mp_face_mesh = mp.solutions.face_mesh

    try:
        with mp_pose.Pose(
            static_image_mode=False,
            model_complexity=0,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        ) as pose, mp_face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        ) as face_mesh:
            while True:
                if end_frame is not None and frame_idx >= end_frame:
                    break

                if frame_idx % frame_interval != 0:
                    if not cap.grab():
                        break
                    frame_idx += 1
                    continue

                ret, frame = cap.read()
                if not ret:
                    break

                timestamp = frame_idx / fps
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                pose_result = pose.process(rgb)
                if pose_result.pose_landmarks and pose_result.pose_landmarks.landmark:
                    lm = pose_result.pose_landmarks.landmark
                    ls_y = lm[mp_pose.PoseLandmark.LEFT_SHOULDER].y
                    rs_y = lm[mp_pose.PoseLandmark.RIGHT_SHOULDER].y
                    left_shoulder_ys.append(ls_y)
                    right_shoulder_ys.append(rs_y)
                    shoulder_diffs.append(abs(ls_y - rs_y))
                else:
                    left_shoulder_ys.append(left_shoulder_ys[-1] if left_shoulder_ys else 0.5)
                    right_shoulder_ys.append(right_shoulder_ys[-1] if right_shoulder_ys else 0.5)
                    shoulder_diffs.append(shoulder_diffs[-1] if shoulder_diffs else 0.0)

                face_result = face_mesh.process(rgb)
                if face_result.multi_face_landmarks and len(face_result.multi_face_landmarks) > 0:
                    fl = face_result.multi_face_landmarks[0].landmark
                    try:
                        r_ratio = _iris_horizontal_ratio(fl[468].x, fl[133].x, fl[33].x)
                        l_ratio = _iris_horizontal_ratio(fl[473].x, fl[362].x, fl[263].x)
                        iris_ratios.append((r_ratio + l_ratio) / 2.0)
                    except (IndexError, AttributeError):
                        iris_ratios.append(None)

                    try:
                        head_yaws.append(_head_yaw_from_face_landmarks(fl))
                    except (IndexError, AttributeError):
                        head_yaws.append(None)
                else:
                    iris_ratios.append(None)
                    head_yaws.append(None)

                yaw_val = head_yaws[-1]
                is_facing = True
                if yaw_val is not None and abs(yaw_val - yaw_offset) > yaw_threshold:
                    is_facing = False
                facing_camera.append(is_facing)

                frame_indices.append(frame_idx)
                timestamps.append(timestamp)
                frame_idx += 1
    finally:
        cap.release()

    return _RawSignals(
        frame_indices=frame_indices,
        timestamps=timestamps,
        left_shoulder_ys=left_shoulder_ys,
        right_shoulder_ys=right_shoulder_ys,
        shoulder_diffs=shoulder_diffs,
        iris_ratios=iris_ratios,
        head_yaws=head_yaws,
        facing_camera=facing_camera,
    )


def _build_three_chunk_ranges(
    *,
    total_frames: int,
    fps: float,
    overlap_seconds: float,
) -> list[tuple[int, int]]:
    duration_sec = total_frames / fps
    t1 = duration_sec / 3.0
    t2 = 2.0 * duration_sec / 3.0
    overlap = max(0.0, overlap_seconds)

    sec_ranges = [
        (0.0, min(duration_sec, t1 + overlap)),
        (max(0.0, t1 - overlap), min(duration_sec, t2 + overlap)),
        (max(0.0, t2 - overlap), duration_sec),
    ]

    ranges: list[tuple[int, int]] = []
    for idx, (start_sec, end_sec) in enumerate(sec_ranges):
        start_frame = max(0, int(math.floor(start_sec * fps)))
        if idx == BODY_LANGUAGE_PARALLEL_CHUNKS_COUNT - 1:
            end_frame = total_frames
        else:
            end_frame = min(total_frames, int(math.ceil(end_sec * fps)))
        if end_frame <= start_frame:
            end_frame = min(total_frames, start_frame + 1)
        ranges.append((start_frame, end_frame))

    return ranges


def _merge_chunk_signals(chunks: list[_RawSignals]) -> _RawSignals:
    entries: list[tuple[float, int, int]] = []
    for chunk_idx, chunk in enumerate(chunks):
        for signal_idx, ts in enumerate(chunk.timestamps):
            entries.append((ts, chunk_idx, signal_idx))

    entries.sort(key=lambda item: (item[0], item[1]))

    seen_time_keys: set[int] = set()
    merged = _RawSignals(
        frame_indices=[],
        timestamps=[],
        left_shoulder_ys=[],
        right_shoulder_ys=[],
        shoulder_diffs=[],
        iris_ratios=[],
        head_yaws=[],
        facing_camera=[],
    )

    for timestamp, chunk_idx, signal_idx in entries:
        time_key = int(round(timestamp / TIMESTAMP_EPSILON_SEC))
        if time_key in seen_time_keys:
            continue
        seen_time_keys.add(time_key)

        source = chunks[chunk_idx]
        merged.frame_indices.append(source.frame_indices[signal_idx])
        merged.timestamps.append(source.timestamps[signal_idx])
        merged.left_shoulder_ys.append(source.left_shoulder_ys[signal_idx])
        merged.right_shoulder_ys.append(source.right_shoulder_ys[signal_idx])
        merged.shoulder_diffs.append(source.shoulder_diffs[signal_idx])
        merged.iris_ratios.append(source.iris_ratios[signal_idx])
        merged.head_yaws.append(source.head_yaws[signal_idx])
        merged.facing_camera.append(source.facing_camera[signal_idx])

    return merged


def _extract_signals_parallel_chunks(
    video_path: str,
    *,
    thresholds: dict,
    fps: float,
    frame_interval: int,
    total_frames: int,
) -> Optional[_RawSignals]:
    ranges = _build_three_chunk_ranges(
        total_frames=total_frames,
        fps=fps,
        overlap_seconds=BODY_LANGUAGE_CHUNK_OVERLAP_SECONDS,
    )

    max_workers = max(1, min(BODY_LANGUAGE_CHUNK_MAX_WORKERS, BODY_LANGUAGE_PARALLEL_CHUNKS_COUNT))
    chunk_results: list[Optional[_RawSignals]] = [None] * BODY_LANGUAGE_PARALLEL_CHUNKS_COUNT

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _extract_signals_for_range,
                    video_path,
                    thresholds=thresholds,
                    fps=fps,
                    frame_interval=frame_interval,
                    start_frame=start,
                    end_frame=end,
                ): idx
                for idx, (start, end) in enumerate(ranges)
            }

            for future in as_completed(futures):
                idx = futures[future]
                result = future.result()
                if result is None:
                    raise RuntimeError(f"Chunk {idx + 1} extraction failed")
                if not result.timestamps:
                    raise RuntimeError(f"Chunk {idx + 1} produced no sampled frames")
                chunk_results[idx] = result
    except Exception:
        logger.warning("Parallel chunk extraction failed; falling back to single-pass", exc_info=True)
        return None

    if any(chunk is None for chunk in chunk_results):
        return None

    return _merge_chunk_signals([chunk for chunk in chunk_results if chunk is not None])


# ---------------------------------------------------------------------------
# Stage B: post-processing and summary build
# ---------------------------------------------------------------------------

def _build_body_language_payload(
    signals: _RawSignals,
    *,
    thresholds: dict,
    calibrated: bool,
) -> Optional[dict]:
    timestamps = signals.timestamps
    if not timestamps:
        logger.warning("No frames extracted from video for body-language analysis")
        return None

    left_shoulder_ys = signals.left_shoulder_ys
    right_shoulder_ys = signals.right_shoulder_ys
    shoulder_diffs = signals.shoulder_diffs
    iris_ratios = signals.iris_ratios
    head_yaws = signals.head_yaws
    facing_camera = signals.facing_camera

    shoulder_dev_thresh = thresholds["shoulder_deviation_threshold"]
    iris_low = thresholds["iris_center_low"]
    iris_high = thresholds["iris_center_high"]
    yaw_offset = thresholds["head_yaw_offset_deg"]

    baseline_left = _rolling_mean(left_shoulder_ys, ROLLING_BASELINE_WINDOW)
    baseline_right = _rolling_mean(right_shoulder_ys, ROLLING_BASELINE_WINDOW)

    posture_stable: list[bool] = []
    for i in range(len(timestamps)):
        dev_left = abs(left_shoulder_ys[i] - baseline_left[i])
        dev_right = abs(right_shoulder_ys[i] - baseline_right[i])
        stable = (dev_left < shoulder_dev_thresh) and (dev_right < shoulder_dev_thresh)
        posture_stable.append(stable)

    eye_contact_flags: list[bool] = []
    for ratio in iris_ratios:
        if ratio is None:
            eye_contact_flags.append(False)
        else:
            eye_contact_flags.append(iris_low <= ratio <= iris_high)

    turned_away_events: list[dict] = []
    run_start: Optional[int] = None
    for i, facing in enumerate(facing_camera):
        if not facing:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None:
                duration = timestamps[i] - timestamps[run_start]
                if duration >= TURNED_AWAY_MIN_DURATION_SEC:
                    turned_away_events.append(
                        {
                            "time_range": f"{_format_ts(timestamps[run_start])}–{_format_ts(timestamps[i])}",
                            "start_sec": round(timestamps[run_start], 1),
                            "end_sec": round(timestamps[i], 1),
                            "duration_sec": round(duration, 1),
                        }
                    )
                run_start = None

    if run_start is not None:
        duration = timestamps[-1] - timestamps[run_start] + SAMPLE_INTERVAL_SEC
        if duration >= TURNED_AWAY_MIN_DURATION_SEC:
            turned_away_events.append(
                {
                    "time_range": f"{_format_ts(timestamps[run_start])}–{_format_ts(timestamps[-1])}",
                    "start_sec": round(timestamps[run_start], 1),
                    "end_sec": round(timestamps[-1], 1),
                    "duration_sec": round(duration, 1),
                }
            )

    unstable_events: list[dict] = []
    u_start: Optional[int] = None
    for i, stable in enumerate(posture_stable):
        if not stable:
            if u_start is None:
                u_start = i
        else:
            if u_start is not None:
                duration = timestamps[i] - timestamps[u_start]
                if duration >= 2.0:
                    unstable_events.append(
                        {
                            "time_range": f"{_format_ts(timestamps[u_start])}–{_format_ts(timestamps[i])}",
                            "start_sec": round(timestamps[u_start], 1),
                            "end_sec": round(timestamps[i], 1),
                            "duration_sec": round(duration, 1),
                        }
                    )
                u_start = None

    if u_start is not None:
        duration = timestamps[-1] - timestamps[u_start] + SAMPLE_INTERVAL_SEC
        if duration >= 2.0:
            unstable_events.append(
                {
                    "time_range": f"{_format_ts(timestamps[u_start])}–{_format_ts(timestamps[-1])}",
                    "start_sec": round(timestamps[u_start], 1),
                    "end_sec": round(timestamps[-1], 1),
                    "duration_sec": round(duration, 1),
                }
            )

    look_away_events: list[dict] = []
    la_start: Optional[int] = None
    for i, contact in enumerate(eye_contact_flags):
        if not contact:
            if la_start is None:
                la_start = i
        else:
            if la_start is not None:
                duration = timestamps[i] - timestamps[la_start]
                if duration >= 2.0:
                    direction = "unknown"
                    ratios_in_range = [
                        iris_ratios[j] for j in range(la_start, i) if iris_ratios[j] is not None
                    ]
                    if ratios_in_range:
                        avg = sum(ratios_in_range) / len(ratios_in_range)
                        if avg < iris_low:
                            direction = "left"
                        elif avg > iris_high:
                            direction = "right"
                        else:
                            direction = "away"
                    look_away_events.append(
                        {
                            "time_range": f"{_format_ts(timestamps[la_start])}–{_format_ts(timestamps[i])}",
                            "start_sec": round(timestamps[la_start], 1),
                            "end_sec": round(timestamps[i], 1),
                            "duration_sec": round(duration, 1),
                            "direction": direction,
                        }
                    )
                la_start = None

    if la_start is not None:
        duration = timestamps[-1] - timestamps[la_start] + SAMPLE_INTERVAL_SEC
        if duration >= 2.0:
            ratios_in_range = [
                iris_ratios[j] for j in range(la_start, len(iris_ratios)) if iris_ratios[j] is not None
            ]
            direction = "unknown"
            if ratios_in_range:
                avg = sum(ratios_in_range) / len(ratios_in_range)
                if avg < iris_low:
                    direction = "left"
                elif avg > iris_high:
                    direction = "right"
                else:
                    direction = "away"
            look_away_events.append(
                {
                    "time_range": f"{_format_ts(timestamps[la_start])}–{_format_ts(timestamps[-1])}",
                    "start_sec": round(timestamps[la_start], 1),
                    "end_sec": round(timestamps[-1], 1),
                    "duration_sec": round(duration, 1),
                    "direction": direction,
                }
            )

    posture_timeline: list[dict] = []
    eye_contact_timeline: list[dict] = []
    facing_timeline: list[dict] = []

    for i in range(len(timestamps)):
        t = round(timestamps[i], 1)
        posture_timeline.append(
            {
                "sec": t,
                "stable": posture_stable[i],
                "shoulder_diff": round(shoulder_diffs[i], 4),
            }
        )
        eye_contact_timeline.append(
            {
                "sec": t,
                "looking_at_camera": eye_contact_flags[i],
                "iris_ratio": round(iris_ratios[i], 3) if iris_ratios[i] is not None else None,
            }
        )
        facing_timeline.append(
            {
                "sec": t,
                "facing_camera": facing_camera[i],
                "head_yaw_deg": round(head_yaws[i], 1) if head_yaws[i] is not None else None,
            }
        )

    n = len(timestamps)
    posture_stability_pct = round(100.0 * sum(posture_stable) / n, 1) if n else 0.0
    eye_contact_pct = round(100.0 * sum(eye_contact_flags) / n, 1) if n else 0.0
    facing_camera_pct = round(100.0 * sum(facing_camera) / n, 1) if n else 0.0
    total_duration_sec = round(timestamps[-1] + SAMPLE_INTERVAL_SEC, 1) if timestamps else 0.0

    summary = {
        "total_duration_sec": total_duration_sec,
        "total_frames_analyzed": n,
        "sample_interval_sec": SAMPLE_INTERVAL_SEC,
        "posture_stability_pct": posture_stability_pct,
        "eye_contact_pct": eye_contact_pct,
        "facing_camera_pct": facing_camera_pct,
        "unstable_event_count": len(unstable_events),
        "look_away_event_count": len(look_away_events),
        "turned_away_event_count": len(turned_away_events),
        "calibrated": calibrated,
    }
    if calibrated:
        summary["calibration_iris_range"] = [round(iris_low, 3), round(iris_high, 3)]
        summary["calibration_shoulder_threshold"] = round(shoulder_dev_thresh, 4)
        summary["calibration_yaw_offset"] = round(yaw_offset, 1)

    return {
        "posture_timeline": posture_timeline,
        "eye_contact_timeline": eye_contact_timeline,
        "facing_timeline": facing_timeline,
        "unstable_events": unstable_events,
        "look_away_events": look_away_events,
        "turned_away_events": turned_away_events,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_body_language_metrics(
    video_path: str | Path,
    calibration: Optional[dict] = None,
) -> Optional[dict]:
    """Analyse *video_path* and return body-language timelines + summary.

    If *calibration* is provided (from a pre-recording selfie), thresholds
    for iris detection, shoulder stability, and head yaw are personalised.

    Returns ``None`` if dependencies are missing or the video cannot be read.
    """
    if not BODY_LANGUAGE_AVAILABLE:
        missing = []
        if not _CV2_AVAILABLE:
            missing.append("opencv-python (pip install opencv-contrib-python)")
        if not _MEDIAPIPE_AVAILABLE:
            missing.append("mediapipe (pip install 'mediapipe>=0.10')")
        logger.error(
            "Body language analysis unavailable — missing: %s",
            ", ".join(missing),
        )
        return None

    resolved_path, converted_mp4, meta = _prepare_video_source(video_path)
    if not resolved_path:
        return None

    thresholds = _build_thresholds(calibration)
    fps = float(meta.get("fps") or 30.0)
    frame_interval = int(meta.get("frame_interval") or max(1, int(round(fps * SAMPLE_INTERVAL_SEC))))
    total_frames = int(meta.get("total_frames") or 0)
    duration_sec = float(meta.get("duration_sec") or 0.0)

    try:
        signals: Optional[_RawSignals] = None

        should_parallelize = (
            BODY_LANGUAGE_PARALLEL_CHUNKS_ENABLED
            and BODY_LANGUAGE_PARALLEL_CHUNKS_COUNT == 3
            and total_frames > 0
            and duration_sec >= BODY_LANGUAGE_CHUNK_MIN_VIDEO_SECONDS
        )

        if should_parallelize:
            signals = _extract_signals_parallel_chunks(
                resolved_path,
                thresholds=thresholds,
                fps=fps,
                frame_interval=frame_interval,
                total_frames=total_frames,
            )
            if signals is not None and not signals.timestamps:
                signals = None

        if signals is None:
            signals = _extract_signals_for_range(
                resolved_path,
                thresholds=thresholds,
                fps=fps,
                frame_interval=frame_interval,
                start_frame=0,
                end_frame=None,
            )

        if signals is None:
            return None

        payload = _build_body_language_payload(
            signals,
            thresholds=thresholds,
            calibrated=calibration is not None,
        )
        return payload
    finally:
        if converted_mp4 is not None:
            shutil.rmtree(converted_mp4.parent, ignore_errors=True)
