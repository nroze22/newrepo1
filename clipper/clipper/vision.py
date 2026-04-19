"""MediaPipe Tasks API wrapper — face detection + landmarks.

Modern MediaPipe (0.10+) dropped the legacy `mp.solutions.*` shims in favor
of the Tasks API. The Tasks API needs a local .task model file. We lazy-
download Google's public models to `~/.cache/clipper/mediapipe/` on first
use so users never have to think about it.
"""

from __future__ import annotations

import math
import os
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_MODEL_URLS = {
    "face_detection": (
        "blaze_face_short_range.tflite",
        "https://storage.googleapis.com/mediapipe-models/"
        "face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite",
    ),
    "face_landmarker": (
        "face_landmarker.task",
        "https://storage.googleapis.com/mediapipe-models/"
        "face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    ),
}


def _cache_dir() -> Path:
    d = Path(os.environ.get("CLIPPER_MODEL_DIR") or Path.home() / ".cache" / "clipper" / "mediapipe")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_model(key: str) -> Path:
    name, url = _MODEL_URLS[key]
    dest = _cache_dir() / name
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as out:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
    tmp.rename(dest)
    return dest


_lock = threading.Lock()
_detector = None
_landmarker = None


def _face_detector():
    global _detector
    with _lock:
        if _detector is not None:
            return _detector
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        model_path = _ensure_model("face_detection")
        options = mp_vision.FaceDetectorOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp_vision.RunningMode.IMAGE,
            min_detection_confidence=0.4,
        )
        _detector = mp_vision.FaceDetector.create_from_options(options)
        return _detector


def _face_landmarker():
    global _landmarker
    with _lock:
        if _landmarker is not None:
            return _landmarker
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        model_path = _ensure_model("face_landmarker")
        options = mp_vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp_vision.RunningMode.IMAGE,
            num_faces=1,
            output_face_blendshapes=False,
        )
        _landmarker = mp_vision.FaceLandmarker.create_from_options(options)
        return _landmarker


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


@dataclass
class FaceHit:
    x: int; y: int; w: int; h: int
    confidence: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0


def detect_faces_rgb(rgb: np.ndarray) -> list[FaceHit]:
    """Detect faces in an RGB numpy image. Returns a list of FaceHit in pixel coords."""
    import mediapipe as mp
    detector = _face_detector()
    img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = detector.detect(img)
    h, w = rgb.shape[:2]
    out: list[FaceHit] = []
    for det in result.detections or []:
        bbox = det.bounding_box
        conf = float(det.categories[0].score) if det.categories else 0.0
        fx = max(0, bbox.origin_x)
        fy = max(0, bbox.origin_y)
        fw = max(1, bbox.width)
        fh = max(1, bbox.height)
        out.append(FaceHit(x=fx, y=fy, w=fw, h=fh, confidence=conf))
    return out


def detect_best_face(rgb: np.ndarray) -> FaceHit | None:
    hits = detect_faces_rgb(rgb)
    if not hits:
        return None
    return max(hits, key=lambda h: h.confidence)


def expression_score(rgb: np.ndarray) -> float:
    """Rough 0..1 smile / openness proxy via Face Landmarker.

    0 = no landmarks or stoic; 1 = big smile + open eyes.
    """
    import mediapipe as mp
    landmarker = _face_landmarker()
    img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = landmarker.detect(img)
    if not result.face_landmarks:
        return 0.0
    lm = result.face_landmarks[0]

    def d(a, b):
        return math.hypot(lm[a].x - lm[b].x, lm[a].y - lm[b].y)

    # Normalize by face height (forehead → chin).
    face_h = max(d(10, 152), 1e-6)
    smile = d(61, 291) / face_h
    openness = d(13, 14) / face_h
    left_eye = d(159, 145) / face_h
    right_eye = d(386, 374) / face_h
    eye_open = (left_eye + right_eye) / 2.0

    smile_score = min(1.0, max(0.0, (smile - 0.35) / 0.3))
    openness_score = min(1.0, max(0.0, openness / 0.08))
    eye_score = min(1.0, max(0.0, (eye_open - 0.015) / 0.025))
    return 0.5 * smile_score + 0.3 * openness_score + 0.2 * eye_score
