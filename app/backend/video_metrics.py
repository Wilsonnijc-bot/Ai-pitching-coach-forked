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
from pathlib import Path
from typing import Optional

logger = logging.getLogger("uvicorn.error")

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------
SAMPLE_INTERVAL_SEC = 0.5          # extract one frame every 0.5 s
SHOULDER_DEVIATION_THRESHOLD = 0.035  # normalised Y deviation from baseline
ROLLING_BASELINE_WINDOW = 10       # number of frames for rolling average (5 s)
IRIS_CENTER_LOW = 0.35             # iris ratio thresholds for "looking at camera"
IRIS_CENTER_HIGH = 0.65
HEAD_YAW_THRESHOLD_DEG = 25.0      # degrees; beyond ⇒ "turned away"
TURNED_AWAY_MIN_DURATION_SEC = 3.0  # consecutive turned-away before flagging


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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
    """Return 0‒1 ratio of where the iris sits between inner (0) and outer (1)
    corners of the eye.  ~0.5 ⇒ looking straight ahead."""
    span = abs(eye_outer_x - eye_inner_x)
    if span < 1e-6:
        return 0.5
    return (iris_center_x - min(eye_inner_x, eye_outer_x)) / span


def _head_yaw_from_face_landmarks(face_landmarks) -> float:
    """Estimate yaw angle (degrees) from face mesh landmarks.

    Uses the nose tip (#1) and left/right ear tragion approximations
    (#234 left side, #454 right side).  Positive yaw = turned right."""
    nose = face_landmarks[1]
    left = face_landmarks[234]
    right = face_landmarks[454]

    # Horizontal distances from nose to each side (in normalised coords)
    d_left = abs(nose.x - left.x)
    d_right = abs(nose.x - right.x)

    total = d_left + d_right
    if total < 1e-6:
        return 0.0

    ratio = (d_right - d_left) / total  # +1 fully right, -1 fully left
    # Map ratio to approximate degrees (atan-based)
    yaw_rad = math.asin(max(-1.0, min(1.0, ratio)))
    return math.degrees(yaw_rad)


def _format_ts(sec: float) -> str:
    """Format seconds as M:SS.s for human-readable timelines."""
    m = int(sec) // 60
    s = sec - m * 60
    return f"{m}:{s:04.1f}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_body_language_metrics(video_path: str | Path) -> Optional[dict]:
    """Analyse *video_path* and return a dict with posture / eye-contact /
    facing timelines plus summary aggregates.

    Returns ``None`` if dependencies are missing or the video cannot be read.
    """
    try:
        import cv2
        import mediapipe as mp
    except ImportError:
        logger.warning(
            "opencv-python / mediapipe not installed — skipping body-language metrics"
        )
        return None

    video_path = str(video_path)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.warning("Could not open video for body-language analysis: %s", video_path)
        return None

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = max(1, int(round(fps * SAMPLE_INTERVAL_SEC)))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    mp_pose = mp.solutions.pose
    mp_face_mesh = mp.solutions.face_mesh

    # Per-frame raw data collectors
    timestamps: list[float] = []
    left_shoulder_ys: list[float] = []
    right_shoulder_ys: list[float] = []
    shoulder_diffs: list[float] = []
    iris_ratios: list[Optional[float]] = []  # None when face not detected
    head_yaws: list[Optional[float]] = []
    facing_camera: list[bool] = []

    try:
        with mp_pose.Pose(
            static_image_mode=False,
            model_complexity=0,  # fastest model — sufficient for shoulder tracking
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        ) as pose, mp_face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=1,
            refine_landmarks=True,   # enables iris landmarks
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        ) as face_mesh:
            frame_idx = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                if frame_idx % frame_interval != 0:
                    frame_idx += 1
                    continue

                timestamp = frame_idx / fps
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                # --- Pose ---
                pose_result = pose.process(rgb)
                if (
                    pose_result.pose_landmarks
                    and pose_result.pose_landmarks.landmark
                ):
                    lm = pose_result.pose_landmarks.landmark
                    ls_y = lm[mp_pose.PoseLandmark.LEFT_SHOULDER].y
                    rs_y = lm[mp_pose.PoseLandmark.RIGHT_SHOULDER].y
                    left_shoulder_ys.append(ls_y)
                    right_shoulder_ys.append(rs_y)
                    shoulder_diffs.append(abs(ls_y - rs_y))
                else:
                    # Carry forward last known or default
                    left_shoulder_ys.append(left_shoulder_ys[-1] if left_shoulder_ys else 0.5)
                    right_shoulder_ys.append(right_shoulder_ys[-1] if right_shoulder_ys else 0.5)
                    shoulder_diffs.append(shoulder_diffs[-1] if shoulder_diffs else 0.0)

                # --- Face Mesh (eye contact + head yaw) ---
                face_result = face_mesh.process(rgb)
                if (
                    face_result.multi_face_landmarks
                    and len(face_result.multi_face_landmarks) > 0
                ):
                    fl = face_result.multi_face_landmarks[0].landmark

                    # Eye contact via iris position
                    # Right eye: inner corner 133, outer corner 33, iris center 468
                    # Left eye:  inner corner 362, outer corner 263, iris center 473
                    try:
                        r_ratio = _iris_horizontal_ratio(
                            fl[468].x, fl[133].x, fl[33].x
                        )
                        l_ratio = _iris_horizontal_ratio(
                            fl[473].x, fl[362].x, fl[263].x
                        )
                        avg_ratio = (r_ratio + l_ratio) / 2.0
                        iris_ratios.append(avg_ratio)
                    except (IndexError, AttributeError):
                        iris_ratios.append(None)

                    # Head yaw
                    try:
                        yaw = _head_yaw_from_face_landmarks(fl)
                        head_yaws.append(yaw)
                    except (IndexError, AttributeError):
                        head_yaws.append(None)
                else:
                    iris_ratios.append(None)
                    head_yaws.append(None)

                # Facing camera: combination of head yaw and body orientation
                yaw_val = head_yaws[-1]
                is_facing = True
                if yaw_val is not None and abs(yaw_val) > HEAD_YAW_THRESHOLD_DEG:
                    is_facing = False
                facing_camera.append(is_facing)

                timestamps.append(timestamp)
                frame_idx += 1
    finally:
        cap.release()

    if not timestamps:
        logger.warning("No frames extracted from video for body-language analysis")
        return None

    # ------------------------------------------------------------------
    # Post-process: Posture stability
    # ------------------------------------------------------------------
    baseline_left = _rolling_mean(left_shoulder_ys, ROLLING_BASELINE_WINDOW)
    baseline_right = _rolling_mean(right_shoulder_ys, ROLLING_BASELINE_WINDOW)

    posture_stable: list[bool] = []
    for i in range(len(timestamps)):
        dev_left = abs(left_shoulder_ys[i] - baseline_left[i])
        dev_right = abs(right_shoulder_ys[i] - baseline_right[i])
        stable = (dev_left < SHOULDER_DEVIATION_THRESHOLD) and (dev_right < SHOULDER_DEVIATION_THRESHOLD)
        posture_stable.append(stable)

    # ------------------------------------------------------------------
    # Post-process: Eye contact
    # ------------------------------------------------------------------
    eye_contact_flags: list[bool] = []
    for ratio in iris_ratios:
        if ratio is None:
            # Face not detected → count as no eye contact
            eye_contact_flags.append(False)
        else:
            eye_contact_flags.append(IRIS_CENTER_LOW <= ratio <= IRIS_CENTER_HIGH)

    # ------------------------------------------------------------------
    # Post-process: Turned-away events (consecutive facing=False > threshold)
    # ------------------------------------------------------------------
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
                    turned_away_events.append({
                        "time_range": f"{_format_ts(timestamps[run_start])}–{_format_ts(timestamps[i])}",
                        "start_sec": round(timestamps[run_start], 1),
                        "end_sec": round(timestamps[i], 1),
                        "duration_sec": round(duration, 1),
                    })
                run_start = None
    # Handle run that extends to end of video
    if run_start is not None:
        duration = timestamps[-1] - timestamps[run_start] + SAMPLE_INTERVAL_SEC
        if duration >= TURNED_AWAY_MIN_DURATION_SEC:
            turned_away_events.append({
                "time_range": f"{_format_ts(timestamps[run_start])}–{_format_ts(timestamps[-1])}",
                "start_sec": round(timestamps[run_start], 1),
                "end_sec": round(timestamps[-1], 1),
                "duration_sec": round(duration, 1),
            })

    # ------------------------------------------------------------------
    # Build posture unstable events (consecutive unstable > 2 s)
    # ------------------------------------------------------------------
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
                    unstable_events.append({
                        "time_range": f"{_format_ts(timestamps[u_start])}–{_format_ts(timestamps[i])}",
                        "start_sec": round(timestamps[u_start], 1),
                        "end_sec": round(timestamps[i], 1),
                        "duration_sec": round(duration, 1),
                    })
                u_start = None
    if u_start is not None:
        duration = timestamps[-1] - timestamps[u_start] + SAMPLE_INTERVAL_SEC
        if duration >= 2.0:
            unstable_events.append({
                "time_range": f"{_format_ts(timestamps[u_start])}–{_format_ts(timestamps[-1])}",
                "start_sec": round(timestamps[u_start], 1),
                "end_sec": round(timestamps[-1], 1),
                "duration_sec": round(duration, 1),
            })

    # ------------------------------------------------------------------
    # Build look-away events (consecutive no eye contact > 2 s)
    # ------------------------------------------------------------------
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
                    # Determine dominant direction from iris ratios
                    direction = "unknown"
                    ratios_in_range = [
                        iris_ratios[j] for j in range(la_start, i) if iris_ratios[j] is not None
                    ]
                    if ratios_in_range:
                        avg = sum(ratios_in_range) / len(ratios_in_range)
                        if avg < IRIS_CENTER_LOW:
                            direction = "left"
                        elif avg > IRIS_CENTER_HIGH:
                            direction = "right"
                        else:
                            direction = "away"
                    look_away_events.append({
                        "time_range": f"{_format_ts(timestamps[la_start])}–{_format_ts(timestamps[i])}",
                        "start_sec": round(timestamps[la_start], 1),
                        "end_sec": round(timestamps[i], 1),
                        "duration_sec": round(duration, 1),
                        "direction": direction,
                    })
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
                if avg < IRIS_CENTER_LOW:
                    direction = "left"
                elif avg > IRIS_CENTER_HIGH:
                    direction = "right"
                else:
                    direction = "away"
            look_away_events.append({
                "time_range": f"{_format_ts(timestamps[la_start])}–{_format_ts(timestamps[-1])}",
                "start_sec": round(timestamps[la_start], 1),
                "end_sec": round(timestamps[-1], 1),
                "duration_sec": round(duration, 1),
                "direction": direction,
            })

    # ------------------------------------------------------------------
    # Build per-frame timeline (sampled at 0.5 s)
    # ------------------------------------------------------------------
    posture_timeline: list[dict] = []
    eye_contact_timeline: list[dict] = []
    facing_timeline: list[dict] = []

    for i in range(len(timestamps)):
        t = round(timestamps[i], 1)
        posture_timeline.append({
            "sec": t,
            "stable": posture_stable[i],
            "shoulder_diff": round(shoulder_diffs[i], 4),
        })
        eye_contact_timeline.append({
            "sec": t,
            "looking_at_camera": eye_contact_flags[i],
            "iris_ratio": round(iris_ratios[i], 3) if iris_ratios[i] is not None else None,
        })
        facing_timeline.append({
            "sec": t,
            "facing_camera": facing_camera[i],
            "head_yaw_deg": round(head_yaws[i], 1) if head_yaws[i] is not None else None,
        })

    # ------------------------------------------------------------------
    # Summary statistics
    # ------------------------------------------------------------------
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
    }

    return {
        "posture_timeline": posture_timeline,
        "eye_contact_timeline": eye_contact_timeline,
        "facing_timeline": facing_timeline,
        "unstable_events": unstable_events,
        "look_away_events": look_away_events,
        "turned_away_events": turned_away_events,
        "summary": summary,
    }
