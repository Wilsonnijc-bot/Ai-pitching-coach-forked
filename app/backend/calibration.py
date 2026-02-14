"""Calibration snapshot processing.

Extracts personal reference data from a front-facing selfie taken before
recording begins.  This data is used in ``video_metrics.py`` to improve
detection accuracy for shoulders, eye contact, and head orientation.

Extracted baselines
-------------------
- **iris_baseline_ratio**: the true "looking at camera" iris horizontal
  ratio for this person (may differ from 0.5 due to face asymmetry,
  camera angle, or lighting).
- **shoulder_baseline_y**: average normalised Y of left+right shoulders
  at neutral standing position.
- **shoulder_baseline_diff**: natural left-right shoulder height gap at
  rest, so the posture detector doesn't penalise a person's natural tilt.
- **head_yaw_baseline_deg**: head yaw at neutral (looking straight) —
  accounts for off-center camera placement.
- **clothing_hsv_ranges**: dominant HSV colour ranges near the shoulder
  region, used as a fallback colour cue when Pose detection drops out.
- **skin_hsv_mean**: average HSV of visible skin (forehead area), used to
  verify face presence when FaceMesh confidence is borderline.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Optional

logger = logging.getLogger("uvicorn.error")

# ---------------------------------------------------------------------------
# Distance estimation constants
# ---------------------------------------------------------------------------
# Average adult face width (ear-to-ear) in metres.
_AVG_FACE_WIDTH_M = 0.15
# Assumed horizontal field-of-view for a typical laptop webcam (degrees).
_ASSUMED_HFOV_DEG = 60.0
# Optimal distance range for body-language analysis (metres).
OPTIMAL_DISTANCE_MIN_M = 0.5
OPTIMAL_DISTANCE_MAX_M = 1.0


def _estimate_distance(
    face_width_ratio: float,
    hfov_deg: float = _ASSUMED_HFOV_DEG,
    face_width_m: float = _AVG_FACE_WIDTH_M,
) -> float:
    """Estimate camera distance in metres from the face width ratio.

    *face_width_ratio* is (face pixel width / image pixel width).  Combined
    with the assumed horizontal FoV and average face width we can solve for
    distance::

        frame_width_at_d = 2 * d * tan(hfov / 2)
        face_width_ratio = face_width_m / frame_width_at_d
        ⇒  d = face_width_m / (2 * face_width_ratio * tan(hfov / 2))
    """
    if face_width_ratio <= 0:
        return 0.0
    half_fov_rad = math.radians(hfov_deg / 2.0)
    return face_width_m / (2.0 * face_width_ratio * math.tan(half_fov_rad))


def _distance_feedback(
    estimated_m: float,
    min_m: float = OPTIMAL_DISTANCE_MIN_M,
    max_m: float = OPTIMAL_DISTANCE_MAX_M,
) -> dict:
    """Return a dict with distance status and human-readable feedback."""
    if estimated_m < min_m:
        return {
            "distance_ok": False,
            "distance_status": "too_close",
            "distance_feedback": f"You seem too close (~{estimated_m:.1f} m). "
                                 f"Please step back to {min_m}–{max_m} m (about arm's length).",
        }
    elif estimated_m > max_m:
        return {
            "distance_ok": False,
            "distance_status": "too_far",
            "distance_feedback": f"You seem too far (~{estimated_m:.1f} m). "
                                 f"Please move closer to {min_m}–{max_m} m (about arm's length).",
        }
    else:
        return {
            "distance_ok": True,
            "distance_status": "ok",
            "distance_feedback": f"Good distance (~{estimated_m:.1f} m). Hold this position during recording.",
        }


def _iris_horizontal_ratio(
    iris_center_x: float,
    eye_inner_x: float,
    eye_outer_x: float,
) -> float:
    span = abs(eye_outer_x - eye_inner_x)
    if span < 1e-6:
        return 0.5
    return (iris_center_x - min(eye_inner_x, eye_outer_x)) / span


def _head_yaw_from_face_landmarks(face_landmarks) -> float:
    nose = face_landmarks[1]
    left = face_landmarks[234]
    right = face_landmarks[454]
    d_left = abs(nose.x - left.x)
    d_right = abs(nose.x - right.x)
    total = d_left + d_right
    if total < 1e-6:
        return 0.0
    ratio = (d_right - d_left) / total
    return math.degrees(math.asin(max(-1.0, min(1.0, ratio))))


def _extract_region_hsv_ranges(
    hsv_image,
    y_start: int,
    y_end: int,
    x_start: int,
    x_end: int,
    *,
    k_clusters: int = 2,
) -> list[dict]:
    """Return dominant HSV colour ranges for a rectangular region."""
    import numpy as np
    region = hsv_image[y_start:y_end, x_start:x_end]
    if region.size == 0:
        return []

    pixels = region.reshape(-1, 3).astype(np.float32)

    try:
        import cv2

        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
        k = min(k_clusters, len(pixels))
        if k < 1:
            return []
        _, labels, centres = cv2.kmeans(
            pixels, k, None, criteria, 5, cv2.KMEANS_PP_CENTERS
        )
        ranges = []
        for i in range(k):
            cluster_pixels = pixels[labels.ravel() == i]
            if len(cluster_pixels) == 0:
                continue
            h_mean, s_mean, v_mean = centres[i]
            h_std = float(np.std(cluster_pixels[:, 0]))
            s_std = float(np.std(cluster_pixels[:, 1]))
            v_std = float(np.std(cluster_pixels[:, 2]))
            ranges.append({
                "h_mean": round(float(h_mean), 1),
                "s_mean": round(float(s_mean), 1),
                "v_mean": round(float(v_mean), 1),
                "h_std": round(h_std, 1),
                "s_std": round(s_std, 1),
                "v_std": round(v_std, 1),
                "pixel_count": int(len(cluster_pixels)),
            })
        return ranges
    except Exception:
        logger.warning("HSV clustering failed", exc_info=True)
        return []


def extract_calibration_data(image_path: str | Path) -> Optional[dict]:
    """Process a calibration selfie and return a dict of baselines.

    Returns ``None`` if the image cannot be read or the person's face /
    pose is not detected.
    """
    try:
        import cv2
        import mediapipe as mp
        import numpy as np
    except ImportError:
        logger.warning("cv2 / mediapipe not installed — cannot extract calibration")
        return None

    image_path = str(image_path)
    frame = cv2.imread(image_path)
    if frame is None:
        logger.warning("Could not read calibration image: %s", image_path)
        return None

    h, w = frame.shape[:2]
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    mp_pose = mp.solutions.pose
    mp_face_mesh = mp.solutions.face_mesh

    result: dict = {}

    # ── Pose: shoulder baselines ──
    with mp_pose.Pose(
        static_image_mode=True,
        model_complexity=1,  # higher accuracy for single image
        min_detection_confidence=0.5,
    ) as pose:
        pose_result = pose.process(rgb)
        if pose_result.pose_landmarks and pose_result.pose_landmarks.landmark:
            lm = pose_result.pose_landmarks.landmark
            ls = lm[mp_pose.PoseLandmark.LEFT_SHOULDER]
            rs = lm[mp_pose.PoseLandmark.RIGHT_SHOULDER]

            result["shoulder_baseline_y_left"] = round(ls.y, 4)
            result["shoulder_baseline_y_right"] = round(rs.y, 4)
            result["shoulder_baseline_y"] = round((ls.y + rs.y) / 2, 4)
            result["shoulder_baseline_diff"] = round(abs(ls.y - rs.y), 4)

            # Extract clothing colour near each shoulder (small box below landmark)
            margin_y = int(h * 0.04)  # ~4% of image height below shoulder
            half_w = int(w * 0.06)    # ~6% of image width around shoulder

            for side, landmark in [("left", ls), ("right", rs)]:
                cx = int(landmark.x * w)
                cy = int(landmark.y * h) + margin_y
                y1 = max(0, cy)
                y2 = min(h, cy + margin_y * 2)
                x1 = max(0, cx - half_w)
                x2 = min(w, cx + half_w)
                colours = _extract_region_hsv_ranges(hsv, y1, y2, x1, x2)
                result[f"clothing_hsv_{side}"] = colours
        else:
            logger.info("Calibration: pose not detected — shoulder baselines unavailable")

    # ── Face Mesh: iris baseline + head yaw + skin colour ──
    with mp_face_mesh.FaceMesh(
        static_image_mode=True,
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
    ) as face_mesh:
        face_result = face_mesh.process(rgb)
        if face_result.multi_face_landmarks and len(face_result.multi_face_landmarks) > 0:
            fl = face_result.multi_face_landmarks[0].landmark

            # Iris baseline — the TRUE ratio when looking at camera
            try:
                r_ratio = _iris_horizontal_ratio(fl[468].x, fl[133].x, fl[33].x)
                l_ratio = _iris_horizontal_ratio(fl[473].x, fl[362].x, fl[263].x)
                avg_ratio = (r_ratio + l_ratio) / 2.0
                result["iris_baseline_ratio"] = round(avg_ratio, 4)
                result["iris_baseline_left"] = round(l_ratio, 4)
                result["iris_baseline_right"] = round(r_ratio, 4)
            except (IndexError, AttributeError):
                logger.info("Calibration: iris landmarks not available")

            # Head yaw baseline
            try:
                yaw = _head_yaw_from_face_landmarks(fl)
                result["head_yaw_baseline_deg"] = round(yaw, 2)
            except (IndexError, AttributeError):
                pass

            # Skin colour from forehead region (between eyebrows and hairline)
            try:
                # Forehead approximation: above nose bridge (#6), between temples
                forehead_y = fl[10].y  # top of forehead landmark
                nose_bridge_y = fl[6].y
                mid_y = (forehead_y + nose_bridge_y) / 2.0
                cx = fl[6].x
                box_h = int(abs(nose_bridge_y - forehead_y) * h * 0.5)
                box_w = int(0.08 * w)
                cy = int(mid_y * h)
                px = int(cx * w)
                skin_region = hsv[
                    max(0, cy - box_h) : min(h, cy + box_h),
                    max(0, px - box_w) : min(w, px + box_w),
                ]
                if skin_region.size > 0:
                    mean_hsv = np.mean(
                        skin_region.reshape(-1, 3).astype(np.float32), axis=0
                    )
                    result["skin_hsv_mean"] = [round(float(v), 1) for v in mean_hsv]
            except (IndexError, AttributeError):
                pass
            # ── Distance estimation from face width ──
            try:
                left_ear = fl[234]   # left side of face
                right_ear = fl[454]  # right side of face
                face_width_px = abs(right_ear.x - left_ear.x) * w
                face_width_ratio = face_width_px / w if w > 0 else 0
                estimated_dist = _estimate_distance(face_width_ratio)

                result["face_width_ratio"] = round(face_width_ratio, 4)
                result["estimated_distance_m"] = round(estimated_dist, 2)

                fb = _distance_feedback(estimated_dist)
                result["distance_ok"] = fb["distance_ok"]
                result["distance_status"] = fb["distance_status"]
                result["distance_feedback"] = fb["distance_feedback"]

                logger.info(
                    "Calibration distance: estimated=%.2f m, face_ratio=%.3f, status=%s",
                    estimated_dist, face_width_ratio, fb["distance_status"],
                )
            except (IndexError, AttributeError):
                logger.info("Calibration: could not estimate distance")
        else:
            logger.info("Calibration: face not detected — iris/yaw baselines unavailable")

    if not result:
        logger.warning("Calibration: no features could be extracted from image")
        return None

    result["image_width"] = w
    result["image_height"] = h
    logger.info(
        "Calibration extracted: %s",
        {k: v for k, v in result.items() if not k.startswith("clothing_hsv")},
    )
    return result
