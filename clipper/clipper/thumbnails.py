"""Thumbnail generation with face-aware frame scoring.

For each clip we:
    1. Sample frames at ~3 fps across the clip.
    2. Score each frame on face presence, face size, sharpness (Laplacian
       variance), exposure (not too dark / blown), and smile/eye openness
       heuristics from MediaPipe Face Mesh.
    3. Pick the top N distinct moments (non-adjacent).
    4. Produce thumbnail PNGs in multiple styles:
         - plain (clean crop)
         - title_overlay (bottom strip with clip title)
         - title_logo (title + brand logo in a corner)

Outputs:
    ThumbnailSet(
        best_frame_ts,
        variants=[{"style": ..., "path": Path, "time": float}, ...]
    )
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .captions import BrandKit

_FACE_DET = None
_FACE_MESH = None


def _face_detector():
    global _FACE_DET
    if _FACE_DET is None:
        import mediapipe as mp
        _FACE_DET = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.5,
        )
    return _FACE_DET


def _face_mesh():
    global _FACE_MESH
    if _FACE_MESH is None:
        import mediapipe as mp
        _FACE_MESH = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
        )
    return _FACE_MESH


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class FrameScore:
    time: float
    score: float
    face_area: float
    sharpness: float
    exposure: float
    expression: float
    face_bbox: tuple[int, int, int, int] | None  # x, y, w, h in pixels


def _sharpness(gray: np.ndarray) -> float:
    # Laplacian variance — higher = sharper.
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    return float(lap.var())


def _exposure(gray: np.ndarray) -> float:
    # Prefer mid-range exposure. Values far from 128 get penalized.
    mean = float(gray.mean())
    clipped = float(((gray < 8).mean()) + ((gray > 247).mean()))
    dev = abs(mean - 128.0) / 128.0
    return max(0.0, 1.0 - dev) * (1.0 - min(clipped * 5.0, 1.0))


def _expression(rgb: np.ndarray) -> float:
    """Rough smile/eye-openness proxy from Face Mesh landmarks.

    Returns 0..1. 0.5 is neutral; >0.7 suggests open expression (good thumbnail).
    """
    try:
        res = _face_mesh().process(rgb)
    except Exception:
        return 0.5
    if not res.multi_face_landmarks:
        return 0.0
    lm = res.multi_face_landmarks[0].landmark

    def dist(a, b):
        return math.hypot(lm[a].x - lm[b].x, lm[a].y - lm[b].y)

    # Mouth openness: upper lip 13 to lower lip 14, normalized by face height.
    mouth_h = dist(13, 14)
    # Smile width: left corner 61, right corner 291.
    mouth_w = dist(61, 291)
    # Face height proxy: 10 (forehead) to 152 (chin).
    face_h = max(dist(10, 152), 1e-6)
    # Eyes openness — left eye upper 159 / lower 145, right 386/374.
    left_eye = dist(159, 145) / face_h
    right_eye = dist(386, 374) / face_h
    eye_open = (left_eye + right_eye) / 2.0

    smile = mouth_w / face_h
    openness = mouth_h / face_h
    # Heuristic: strong smile ~0.55, wide open mouth ~0.05+.
    smile_score = min(1.0, max(0.0, (smile - 0.35) / 0.3))
    openness_score = min(1.0, max(0.0, openness / 0.08))
    eye_score = min(1.0, max(0.0, (eye_open - 0.015) / 0.025))
    return 0.5 * smile_score + 0.3 * openness_score + 0.2 * eye_score


def score_frame(frame: np.ndarray, t: float) -> FrameScore:
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    sharp = _sharpness(gray)
    expo = _exposure(gray)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    det = _face_detector().process(rgb)
    face_bbox: tuple[int, int, int, int] | None = None
    face_area = 0.0
    if det.detections:
        d = max(det.detections, key=lambda x: (x.score[0] if x.score else 0))
        bb = d.location_data.relative_bounding_box
        fx, fy = max(0, int(bb.xmin * w)), max(0, int(bb.ymin * h))
        fw, fh = max(1, int(bb.width * w)), max(1, int(bb.height * h))
        face_bbox = (fx, fy, fw, fh)
        face_area = min(1.0, (fw * fh) / (w * h) * 4.0)  # scaled: 25% is a great fill
    expr = _expression(rgb) if face_bbox else 0.0

    # Combined score — tuned defaults.
    sharp_norm = min(1.0, sharp / 400.0)
    score = (
        0.40 * face_area
        + 0.25 * expr
        + 0.20 * sharp_norm
        + 0.15 * expo
    )
    return FrameScore(
        time=t, score=score,
        face_area=face_area, sharpness=sharp_norm,
        exposure=expo, expression=expr, face_bbox=face_bbox,
    )


def _sample(video: Path, start: float, end: float, fps: float = 3.0):
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open {video}")
    try:
        native = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, int(round(native / fps)))
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
        out = []
        while True:
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if t >= end:
                break
            ok, frame = cap.read()
            if not ok:
                break
            out.append((t, frame))
            for _ in range(step - 1):
                if not cap.grab():
                    break
        return out
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _load_font(size: int) -> ImageFont.ImageFont:
    # Prefer bundled font options; fall back to default.
    for candidate in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/Library/Fonts/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _bgr_to_rgb_image(frame: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def _crop_thumbnail(frame: np.ndarray, aspect: tuple[int, int], face: tuple[int, int, int, int] | None) -> np.ndarray:
    """Center-or-face-biased crop to target aspect ratio."""
    h, w = frame.shape[:2]
    tw, th = aspect
    cur = w / h
    tar = tw / th
    if abs(cur - tar) < 0.01:
        return frame
    if cur > tar:
        new_w = int(h * tar)
        if face:
            cx = face[0] + face[2] // 2
            x1 = max(0, min(cx - new_w // 2, w - new_w))
        else:
            x1 = (w - new_w) // 2
        return frame[:, x1:x1 + new_w]
    else:
        new_h = int(w / tar)
        y1 = max(0, (h - new_h) // 2)
        return frame[y1:y1 + new_h, :]


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        cand = " ".join(cur + [w])
        if draw.textlength(cand, font=font) <= max_width:
            cur.append(w)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines[:3]


def _render_title_overlay(img: Image.Image, title: str, kit: BrandKit) -> Image.Image:
    out = img.copy().convert("RGBA")
    w, h = out.size
    font = _load_font(max(36, int(h * 0.055)))
    draw = ImageDraw.Draw(out)
    # Gradient bar bottom third.
    bar = Image.new("RGBA", (w, int(h * 0.35)), (0, 0, 0, 0))
    bar_draw = ImageDraw.Draw(bar)
    for y in range(bar.height):
        alpha = int(255 * (y / bar.height) * 0.9)
        bar_draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))
    out.alpha_composite(bar, (0, h - bar.height))
    # Title text — wrap.
    margin = int(w * 0.06)
    max_text_w = w - 2 * margin
    lines = _wrap_text(draw, title.upper() if kit.uppercase else title, font, max_text_w)
    line_h = int(font.size * 1.1)
    total_h = line_h * len(lines)
    y = h - margin - total_h
    color_hex = kit.primary_color.lstrip("#")
    r, g, b = int(color_hex[0:2], 16), int(color_hex[2:4], 16), int(color_hex[4:6], 16)
    for line in lines:
        draw.text((margin + 2, y + 2), line, font=font, fill=(0, 0, 0, 200))
        draw.text((margin, y), line, font=font, fill=(r, g, b, 255))
        y += line_h
    return out.convert("RGB")


def _render_logo(img: Image.Image, kit: BrandKit) -> Image.Image:
    if not kit.logo_path or not Path(kit.logo_path).exists():
        return img
    out = img.copy().convert("RGBA")
    try:
        logo = Image.open(kit.logo_path).convert("RGBA")
    except Exception:
        return img
    w, h = out.size
    target_w = int(w * 0.18)
    ratio = target_w / max(logo.width, 1)
    logo = logo.resize((target_w, max(1, int(logo.height * ratio))), Image.LANCZOS)
    # Adjust opacity.
    if kit.logo_opacity < 1.0:
        alpha = logo.split()[-1]
        alpha = alpha.point(lambda p: int(p * kit.logo_opacity))
        logo.putalpha(alpha)
    margin = int(min(w, h) * 0.04)
    positions = {
        "top_left": (margin, margin),
        "top_right": (w - logo.width - margin, margin),
        "bottom_left": (margin, h - logo.height - margin),
        "bottom_right": (w - logo.width - margin, h - logo.height - margin),
    }
    pos = positions.get(kit.logo_position, positions["top_right"])
    out.alpha_composite(logo, pos)
    return out.convert("RGB")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class ThumbnailVariant:
    style: str
    path: Path
    time: float
    score: float


@dataclass
class ThumbnailSet:
    clip_id: int | None = None
    variants: list[ThumbnailVariant] = field(default_factory=list)
    best_time: float | None = None
    frames_sampled: int = 0
    faces_found: int = 0

    @property
    def hero(self) -> Path | None:
        return self.variants[0].path if self.variants else None

    def to_dict(self) -> dict:
        return {
            "clip_id": self.clip_id,
            "best_time": self.best_time,
            "frames_sampled": self.frames_sampled,
            "faces_found": self.faces_found,
            "variants": [
                {"style": v.style, "path": str(v.path), "time": v.time, "score": v.score}
                for v in self.variants
            ],
        }


def _pick_top(scores: list[FrameScore], *, n: int = 3, min_gap_sec: float = 1.5) -> list[FrameScore]:
    chosen: list[FrameScore] = []
    for s in sorted(scores, key=lambda x: x.score, reverse=True):
        if all(abs(s.time - c.time) >= min_gap_sec for c in chosen):
            chosen.append(s)
        if len(chosen) >= n:
            break
    return chosen


def generate_thumbnails(
    video_path: Path,
    clip_start: float,
    clip_end: float,
    *,
    title: str,
    out_dir: Path,
    slug: str,
    aspect: tuple[int, int] = (16, 9),
    brand_kit: BrandKit | None = None,
    n_variants: int = 3,
    sample_fps: float = 3.0,
) -> ThumbnailSet:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    kit = brand_kit or BrandKit()

    samples = _sample(video_path, clip_start, clip_end, fps=sample_fps)
    if not samples:
        return ThumbnailSet(frames_sampled=0)

    scores = [score_frame(frame, t - clip_start) for t, frame in samples]
    faces_found = sum(1 for s in scores if s.face_bbox is not None)
    picks = _pick_top(scores, n=n_variants, min_gap_sec=max(1.5, (clip_end - clip_start) / (n_variants + 1)))
    if not picks:
        picks = [scores[len(scores) // 2]]

    result = ThumbnailSet(
        best_time=picks[0].time,
        frames_sampled=len(scores),
        faces_found=faces_found,
    )

    # Render three variants from the top pick, then a second clean frame for comparison.
    top_t, top_frame = samples[scores.index(picks[0])]
    primary_face = picks[0].face_bbox
    cropped = _crop_thumbnail(top_frame, aspect, primary_face)
    pil = _bgr_to_rgb_image(cropped)

    plain_path = out_dir / f"{slug}__plain.jpg"
    pil.save(plain_path, "JPEG", quality=92)
    result.variants.append(ThumbnailVariant("plain", plain_path, picks[0].time, picks[0].score))

    if title:
        overlay = _render_title_overlay(pil, title, kit)
        overlay_path = out_dir / f"{slug}__title.jpg"
        overlay.save(overlay_path, "JPEG", quality=92)
        result.variants.append(ThumbnailVariant("title", overlay_path, picks[0].time, picks[0].score))

        if kit.logo_path and Path(kit.logo_path).exists():
            branded = _render_logo(overlay, kit)
            branded_path = out_dir / f"{slug}__branded.jpg"
            branded.save(branded_path, "JPEG", quality=92)
            result.variants.append(ThumbnailVariant("branded", branded_path, picks[0].time, picks[0].score))

    # Alternate moment variant — gives the creator a choice.
    if len(picks) > 1:
        alt_t, alt_frame = samples[scores.index(picks[1])]
        alt_pil = _bgr_to_rgb_image(_crop_thumbnail(alt_frame, aspect, picks[1].face_bbox))
        alt_path = out_dir / f"{slug}__alt.jpg"
        alt_pil.save(alt_path, "JPEG", quality=92)
        result.variants.append(ThumbnailVariant("alt", alt_path, picks[1].time, picks[1].score))

    return result
